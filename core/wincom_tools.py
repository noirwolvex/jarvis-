from __future__ import annotations

import json
import os
import re
from typing import Any

from .execution_telemetry import record_backend
from .permissions import Risk
from .tools import ToolSpec


_PROG_IDS = {
    "word": "Word.Application",
    "excel": "Excel.Application",
    "powerpoint": "PowerPoint.Application",
}
_CELL_RE = re.compile(r"^\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}$", re.IGNORECASE)


def _windows_only() -> None:
    if os.name != "nt":
        raise RuntimeError("WinCOM tools are available only on Windows")


def _active_application(application: str):
    _windows_only()
    key = str(application or "").strip().casefold()
    if key not in _PROG_IDS:
        raise ValueError("Unsupported WinCOM application")
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pywin32 is required for WinCOM application adapters") from exc
    try:
        app = win32com.client.GetActiveObject(_PROG_IDS[key])
    except Exception as exc:
        raise RuntimeError(f"No active {key} COM application is available") from exc
    record_backend("wincom", phase="resolve", detail=f"Attach active {key} application")
    return key, app


def _bounded_text(value: Any, limit: int = 4000) -> str:
    return str(value or "")[:limit]


def wincom_inspect(application: str) -> str:
    key, app = _active_application(application)
    if key == "word":
        document = app.ActiveDocument
        payload = {
            "application": "word",
            "document": _bounded_text(getattr(document, "Name", "")),
            "full_name": _bounded_text(getattr(document, "FullName", "")),
            "saved": bool(getattr(document, "Saved", False)),
            "characters": int(getattr(document.Characters, "Count", 0)),
        }
    elif key == "excel":
        workbook = app.ActiveWorkbook
        sheet = app.ActiveSheet
        cell = app.ActiveCell
        payload = {
            "application": "excel",
            "workbook": _bounded_text(getattr(workbook, "Name", "")),
            "sheet": _bounded_text(getattr(sheet, "Name", "")),
            "active_cell": _bounded_text(getattr(cell, "Address", "")),
            "saved": bool(getattr(workbook, "Saved", False)),
        }
    else:
        presentation = app.ActivePresentation
        slide_index = None
        try:
            slide_index = int(app.ActiveWindow.View.Slide.SlideIndex)
        except Exception:
            pass
        payload = {
            "application": "powerpoint",
            "presentation": _bounded_text(getattr(presentation, "Name", "")),
            "full_name": _bounded_text(getattr(presentation, "FullName", "")),
            "saved": bool(getattr(presentation, "Saved", False)),
            "active_slide": slide_index,
        }
    record_backend("wincom", phase="observe", detail=f"Read active {key} state")
    return "VERIFIED: " + json.dumps(payload, ensure_ascii=False)


def wincom_word_append(text: str) -> str:
    value = str(text)
    if not value or len(value) > 20000 or "\x00" in value:
        raise ValueError("Word text must contain 1-20000 characters and no NUL")
    _, app = _active_application("word")
    document = app.ActiveDocument
    record_backend("wincom", phase="execute", detail="Append text to active Word document")
    document.Content.InsertAfter(value)
    observed = str(document.Content.Text or "").rstrip("\r\x07")
    expected = value.rstrip("\r\x07")
    record_backend("wincom", phase="verify", detail="Read back Word document tail")
    if expected and not observed.endswith(expected):
        raise RuntimeError("Word COM write did not pass exact tail readback")
    return f"VERIFIED: appended {len(value)} characters to active Word document"


def wincom_excel_set_cell(cell: str, value: Any, sheet: str = "") -> str:
    address = str(cell or "").strip().upper()
    if not _CELL_RE.fullmatch(address):
        raise ValueError("Excel cell must be a valid A1-style address")
    _, app = _active_application("excel")
    workbook = app.ActiveWorkbook
    target_sheet = workbook.Worksheets(str(sheet)) if str(sheet).strip() else app.ActiveSheet
    target = target_sheet.Range(address)
    record_backend("wincom", phase="execute", detail=f"Set Excel cell {address}")
    target.Value2 = value
    observed = target.Value2
    record_backend("wincom", phase="verify", detail=f"Read back Excel cell {address}")
    if str(observed) != str(value):
        raise RuntimeError("Excel COM write did not pass exact cell readback")
    return "VERIFIED: " + json.dumps({
        "workbook": _bounded_text(getattr(workbook, "Name", "")),
        "sheet": _bounded_text(getattr(target_sheet, "Name", "")),
        "cell": address,
        "value": observed,
    }, ensure_ascii=False)


def wincom_powerpoint_set_text(slide_index: int, shape_name: str, text: str) -> str:
    index = int(slide_index)
    name = str(shape_name or "").strip()
    value = str(text)
    if index < 1 or index > 10000:
        raise ValueError("PowerPoint slide_index must be between 1 and 10000")
    if not name or len(name) > 256:
        raise ValueError("PowerPoint shape_name is required")
    if len(value) > 20000 or "\x00" in value:
        raise ValueError("PowerPoint text exceeds safety limits")
    _, app = _active_application("powerpoint")
    presentation = app.ActivePresentation
    slide = presentation.Slides(index)
    shape = slide.Shapes.Item(name)
    if not bool(getattr(shape, "HasTextFrame", False)):
        raise ValueError("Target PowerPoint shape has no text frame")
    record_backend("wincom", phase="execute", detail=f"Set PowerPoint slide {index} shape text")
    shape.TextFrame.TextRange.Text = value
    observed = str(shape.TextFrame.TextRange.Text or "")
    record_backend("wincom", phase="verify", detail="Read back PowerPoint shape text")
    if observed != value:
        raise RuntimeError("PowerPoint COM write did not pass exact text readback")
    return "VERIFIED: " + json.dumps({
        "presentation": _bounded_text(getattr(presentation, "Name", "")),
        "slide_index": index,
        "shape_name": name,
        "characters": len(value),
    }, ensure_ascii=False)


def register_wincom_tools(registry) -> None:
    registry.register(ToolSpec(
        "wincom_inspect",
        "Read the active Microsoft Word, Excel, or PowerPoint application through native Windows COM. Prefer this read-only API path before UIA/vision when the task concerns an already-open supported Office app.",
        Risk.SAFE,
        {
            "type": "object",
            "properties": {"application": {"type": "string", "enum": ["word", "excel", "powerpoint"]}},
            "required": ["application"],
            "additionalProperties": False,
        },
        wincom_inspect,
    ))
    registry.register(ToolSpec(
        "wincom_word_append",
        "Append text to the active Word document through WinCOM and verify the written document tail. Does not save the document.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 20000}},
            "required": ["text"],
            "additionalProperties": False,
        },
        wincom_word_append,
    ))
    registry.register(ToolSpec(
        "wincom_excel_set_cell",
        "Set one cell in the active Excel workbook through WinCOM and verify the exact cell value. Does not save the workbook.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "cell": {"type": "string", "pattern": r"^\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,6}$"},
                "value": {"type": ["string", "number", "boolean", "null"]},
                "sheet": {"type": "string", "maxLength": 256},
            },
            "required": ["cell", "value"],
            "additionalProperties": False,
        },
        wincom_excel_set_cell,
    ))
    registry.register(ToolSpec(
        "wincom_powerpoint_set_text",
        "Set text in one named shape on one slide of the active PowerPoint presentation through WinCOM and verify exact readback. Does not save the presentation.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "slide_index": {"type": "integer", "minimum": 1, "maximum": 10000},
                "shape_name": {"type": "string", "minLength": 1, "maxLength": 256},
                "text": {"type": "string", "maxLength": 20000},
            },
            "required": ["slide_index", "shape_name", "text"],
            "additionalProperties": False,
        },
        wincom_powerpoint_set_text,
    ))
