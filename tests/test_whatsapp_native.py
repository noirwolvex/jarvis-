from __future__ import annotations

import unittest
from unittest.mock import patch

import core.whatsapp_native as whatsapp


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Control:
    def __init__(
        self,
        name: str,
        control_type: str,
        rect: tuple[int, int, int, int],
        parent=None,
        visible: bool = True,
        enabled: bool = True,
    ):
        self.name = name
        self.control_type = control_type
        self.rect = list(rect)
        self._parent = parent
        self._visible = visible
        self._enabled = enabled

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def parent(self):
        return self._parent


class _Window:
    def __init__(self, controls):
        self.controls = controls

    def rectangle(self):
        return _Rect(0, 0, 1200, 800)


def _name(control):
    return control.name


def _type(control):
    return control.control_type


def _rect(control):
    return list(control.rect)


class WhatsAppNativeRowDiscoveryTests(unittest.TestCase):
    def test_modern_custom_rows_are_resolved_from_named_text_descendants(self) -> None:
        first_row = _Control("", "Custom", (70, 130, 545, 200))
        first_name = _Control("Alice", "Text", (118, 145, 260, 168), parent=first_row)
        first_preview = _Control("hello", "Text", (118, 171, 340, 190), parent=first_row)

        second_row = _Control("", "Group", (70, 205, 545, 275))
        second_name = _Control("Bob", "Text", (118, 220, 250, 244), parent=second_row)
        second_preview = _Control("meeting at 4", "Text", (118, 246, 355, 266), parent=second_row)

        nav_row = _Control("", "Custom", (70, 70, 545, 120))
        nav_label = _Control("Chats", "Text", (110, 82, 190, 105), parent=nav_row)

        win = _Window([
            nav_label,
            nav_row,
            first_name,
            first_preview,
            first_row,
            second_name,
            second_preview,
            second_row,
        ])

        with patch("core.semantic_ui_tools._descendants", return_value=win.controls), \
             patch("core.semantic_ui_tools._control_name", side_effect=_name), \
             patch("core.semantic_ui_tools._control_type", side_effect=_type), \
             patch("core.semantic_ui_tools._rect", side_effect=_rect):
            candidates = whatsapp._chat_candidates(win, 2)

        self.assertEqual(candidates, [first_row, second_row])

    def test_duplicate_text_children_do_not_create_duplicate_chat_positions(self) -> None:
        row = _Control("", "Custom", (70, 130, 545, 200))
        title = _Control("Alice", "Text", (118, 145, 260, 168), parent=row)
        preview = _Control("two child controls in the same visual row", "Text", (118, 171, 430, 190), parent=row)
        win = _Window([title, preview, row])

        with patch("core.semantic_ui_tools._descendants", return_value=win.controls), \
             patch("core.semantic_ui_tools._control_name", side_effect=_name), \
             patch("core.semantic_ui_tools._control_type", side_effect=_type), \
             patch("core.semantic_ui_tools._rect", side_effect=_rect):
            candidates = whatsapp._chat_candidates(win, 1)

        self.assertEqual(candidates, [row])


if __name__ == "__main__":
    unittest.main()
