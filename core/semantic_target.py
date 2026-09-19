"""Deterministic selection from one fresh, detached UI Automation tree."""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

_ANCHOR = {"type": "object", "properties": {
    "target": {"type": "string", "minLength": 1, "maxLength": 500},
    "control_type": {"type": "string", "maxLength": 80},
}, "required": ["target"], "additionalProperties": False}
SELECTOR_SCHEMA = {"type": "object", "properties": {
    "ordinal": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 700}, {"const": "last"}]},
    "selected": {"type": "boolean"}, "focused": {"type": "boolean"},
    "parent": _ANCHOR,
    "adjacent": {"type": "object", "properties": {
        **_ANCHOR["properties"],
        "direction": {"type": "string", "enum": ["left", "right", "above", "below"]},
    }, "required": ["target", "direction"], "additionalProperties": False},
}, "minProperties": 1, "additionalProperties": False}


def _matches(row: dict, target: str, control_type: str) -> bool:
    return (not control_type or row["type"].casefold() == control_type.casefold()) and (
        not target or target.casefold() in {row["name"].casefold(), row["automation_id"].casefold()})


def select(rows: list[dict[str, Any]], target: str, control_type: str,
           selector: dict[str, Any], *, editable: bool = False) -> dict[str, Any]:
    """No fuzzy guesses, cross-container ordinals, or ambiguous geometric ties."""
    Draft202012Validator(SELECTOR_SCHEMA).validate(selector)
    if "ordinal" in selector and not control_type and not editable:
        raise ValueError("Ordinal selection requires an explicit control_type")
    visible = [row for row in rows if row.get("visible") is True and row.get("enabled") is True]
    matches = [row for row in visible if _matches(row, target, control_type)
               and (not editable or row["type"] in {"Edit", "Document"})]
    for state in ("selected", "focused"):
        if state in selector:
            matches = [row for row in matches if row.get(state) is selector[state]]
    if "parent" in selector:
        scope = selector["parent"]
        parents = [row for row in visible if _matches(row, scope["target"], scope.get("control_type", ""))]
        if len(parents) != 1:
            raise RuntimeError("Parent scope must match exactly one visible enabled container")
        matches = [row for row in matches if parents[0]["identity"] in row["ancestors"]]
    if "adjacent" in selector:
        relation = selector["adjacent"]
        anchors = [row for row in visible if _matches(row, relation["target"], relation.get("control_type", ""))]
        if len(anchors) != 1:
            raise RuntimeError("Adjacent anchor must match exactly one visible enabled control")
        anchor = anchors[0]
        left, top, right, bottom = anchor["rect"]
        distances = []
        for row in matches:
            if row["identity"] == anchor["identity"] or not row.get("parent_identity") or row["parent_identity"] != anchor.get("parent_identity"):
                continue
            x1, y1, x2, y2 = row["rect"]
            horizontal_overlap = max(left, x1) < min(right, x2)
            vertical_overlap = max(top, y1) < min(bottom, y2)
            direction = relation["direction"]
            distance = None
            if direction == "left" and x2 <= left and vertical_overlap:
                distance = left - x2
            elif direction == "right" and x1 >= right and vertical_overlap:
                distance = x1 - right
            elif direction == "above" and y2 <= top and horizontal_overlap:
                distance = top - y2
            elif direction == "below" and y1 >= bottom and horizontal_overlap:
                distance = y1 - bottom
            if distance is not None:
                distances.append((distance, row))
        if not distances:
            raise RuntimeError("No adjacent semantic control in the anchor's container")
        nearest = min(item[0] for item in distances)
        matches = [row for distance, row in distances if distance == nearest]
        if len(matches) != 1:
            raise RuntimeError("Adjacent semantic target is ambiguous")
    if "ordinal" in selector:
        if not matches:
            raise RuntimeError("No visible enabled control matches the ordinal selector")
        parents = {row.get("parent_identity") for row in matches}
        if len(parents) != 1 or None in parents:
            raise RuntimeError("Ordinal controls span uncertain containers; specify an exact parent scope")
        if any(row["rect"][2] <= row["rect"][0] or row["rect"][3] <= row["rect"][1] for row in matches):
            raise RuntimeError("Ordinal control geometry is unavailable")
        # Visible reading order, independent of provider enumeration/recycling order.
        matches.sort(key=lambda row: (row["rect"][1], row["rect"][0]))
        positions = [(row["rect"][1], row["rect"][0]) for row in matches]
        if len(set(positions)) != len(positions):
            raise RuntimeError("Ordinal controls overlap at the same position; inspect an exact identity")
        ordinal = selector["ordinal"]
        index = len(matches) - 1 if ordinal == "last" else ordinal - 1
        if index >= len(matches):
            raise RuntimeError(f"Ordinal {ordinal} exceeds {len(matches)} visible matching controls")
        return matches[index]
    if len(matches) != 1:
        raise RuntimeError(f"Selector matched {len(matches)} exact visible enabled controls; refine the scope")
    return matches[0]
