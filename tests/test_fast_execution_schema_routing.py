from __future__ import annotations

import unittest

from core.discord_tools import register_discord_tools
from core.browser_fast_tools import register_browser_fast_tools
from core.fast_execution_agent import FastExecutionFullAccessAgent
from core.semantic_ui_tools import register_semantic_ui_tools
from core.tools import ToolRegistry
from core.youtube_fast_tools import register_youtube_fast_tools
from core.whatsapp_native import register_whatsapp_native_tools


class FastExecutionSchemaRoutingTests(unittest.TestCase):
    def _agent(self):
        agent = FastExecutionFullAccessAgent.__new__(FastExecutionFullAccessAgent)
        agent.tools = ToolRegistry()
        register_semantic_ui_tools(agent.tools)
        register_browser_fast_tools(agent.tools)
        register_discord_tools(agent.tools)
        register_youtube_fast_tools(agent.tools)
        register_whatsapp_native_tools(agent.tools)
        return agent

    def test_desktop_media_mission_gets_compact_semantic_profile(self) -> None:
        agent = self._agent()
        schemas = agent._tool_schemas_for_goal(
            "open discord go to Space Zone send hello then open this song on youtube"
        )
        names = {str(schema.get("function", {}).get("name", "")) for schema in schemas}
        self.assertIn("discord_navigate_and_send", names)
        self.assertIn("youtube_search_open", names)
        self.assertIn("ui_batch", names)
        self.assertNotIn("run_powershell", names)
        self.assertNotIn("write_file", names)

    def test_mixed_whatsapp_google_mission_keeps_native_whatsapp_tool(self) -> None:
        agent = self._agent()
        schemas = agent._tool_schemas_for_goal(
            "open WhatsApp and press the first chat, then open Google and search for cats"
        )
        names = {str(schema.get("function", {}).get("name", "")) for schema in schemas}
        self.assertIn("whatsapp_select_chat_native", names)
        self.assertIn("google_search", names)
        self.assertIn("ui_batch", names)

    def test_developer_mission_keeps_full_toolset(self) -> None:
        agent = self._agent()
        schemas = agent._tool_schemas_for_goal("open vscode and edit a code file then git status")
        names = {str(schema.get("function", {}).get("name", "")) for schema in schemas}
        self.assertIn("run_powershell", names)
        self.assertIn("write_file", names)


if __name__ == "__main__":
    unittest.main()
