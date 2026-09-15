from __future__ import annotations

import unittest

from core.discord_tools import register_discord_tools
from core.tools import ToolRegistry


class DiscordToolsTests(unittest.TestCase):
    def test_compound_discord_tools_register_as_medium_risk(self) -> None:
        registry = ToolRegistry()
        register_discord_tools(registry)
        for name in ("discord_go_to", "discord_send_message", "discord_navigate_and_send"):
            self.assertIn(name, registry._tools)
            self.assertEqual(registry._tools[name].risk.name, "MEDIUM")

    def test_compound_schema_requires_destination_and_text(self) -> None:
        registry = ToolRegistry()
        register_discord_tools(registry)
        schema = registry._tools["discord_navigate_and_send"].input_schema
        self.assertEqual(set(schema["required"]), {"destination", "text"})
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
