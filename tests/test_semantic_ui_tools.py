from __future__ import annotations

import unittest

from core.semantic_ui_tools import _find_control, _score, register_semantic_ui_tools
from core.tools import ToolRegistry


class _Info:
    def __init__(self, name: str, control_type: str, automation_id: str = "") -> None:
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Control:
    def __init__(self, name: str, control_type: str, *, automation_id: str = "", top: int = 0, bottom: int = 100) -> None:
        self.element_info = _Info(name, control_type, automation_id)
        self._rect = _Rect(0, top, 500, bottom)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def rectangle(self):
        return self._rect

    def window_text(self):
        return self.element_info.name


class _Window:
    def __init__(self, controls):
        self._controls = controls

    def descendants(self):
        return list(self._controls)


class SemanticUiToolsTests(unittest.TestCase):
    def test_exact_semantic_name_beats_partial_match(self) -> None:
        exact = _Control("Space Zone", "Button")
        partial = _Control("Space Zone Games", "Button")
        win = _Window([partial, exact])
        self.assertIs(_find_control(win, "Space Zone"), exact)
        self.assertGreater(_score(exact, "Space Zone"), _score(partial, "Space Zone"))

    def test_auto_edit_prefers_named_message_composer(self) -> None:
        search = _Control("Search", "Edit", top=50, bottom=90)
        composer = _Control("Message #general", "Edit", top=800, bottom=850)
        win = _Window([search, composer])
        self.assertIs(_find_control(win, editable=True), composer)

    def test_tools_register_with_compound_batch(self) -> None:
        registry = ToolRegistry()
        register_semantic_ui_tools(registry)
        for name in ("ui_inspect", "ui_focus", "ui_activate", "ui_type", "ui_hotkey", "ui_batch"):
            self.assertIn(name, registry._tools)
        self.assertEqual(registry._tools["ui_batch"].risk.name, "MEDIUM")


if __name__ == "__main__":
    unittest.main()
