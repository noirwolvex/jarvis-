from __future__ import annotations

from typing import Callable

from .agent import AgentEvent
from .fast_mission import execute_fast_mission
from .full_access_agent import FullAccessJarvisAgent


class FastExecutionFullAccessAgent(FullAccessJarvisAgent):
    """Full Access agent with a deterministic fast lane and lower-latency model behavior."""

    def _system_prompt(self, user_text: str = "") -> str:
        return super()._system_prompt(user_text) + """

High-speed autonomous execution rules:
- Minimize model round-trips. When the arguments for several safe semantic/direct tools are already known, emit the whole executable batch in the SAME assistant tool-call response. The runtime will execute them sequentially and preserve verification boundaries.
- Do not spend a separate model turn merely restating a plan. When task_plan is useful, emit task_plan together with the first immediately executable verified actions whenever their arguments do not depend on unknown future observations.
- Prefer deterministic compound tools that complete an entire user clause in one verified call. In particular, when the user asks for a Google search followed by the first result/link, use browser_google_search_first_result rather than separate search/read/click actions.
- For labeled Windows desktop interfaces, use ui_activate/ui_type/ui_hotkey or ui_batch before any coordinate click. ui_inspect is the compact semantic observation path; screen_observe is a fallback for canvas/unlabeled/ambiguous visual state only.
- If several already-known Windows UI actions are in the same application, combine them with ui_batch so focus/click/type/hotkey actions do not require a model turn between each one.
- For Discord, prefer discord_go_to, discord_send_message, or discord_navigate_and_send over screenshots, server-icon coordinates, or manual mouse navigation. Never resend an uncertain message automatically.
- For a requested YouTube song/video search, prefer youtube_search_open so search + result selection + watch-page verification happen in one CDP call rather than visual browser navigation.
- After a tool returns VERIFIED evidence, continue to the next already-determined semantic action without taking a redundant screenshot or asking the model to reconsider the same step.
- Use fresh vision/UI inspection only when the next action genuinely depends on visual state, unlabeled controls, or a changed/uncertain scene.
- Long missions must preserve the exact requested order and every clause. Never silently skip a step because later steps succeeded.
- If a deterministic path fails, inspect the resulting live state and recover from the failed step; do not restart the whole mission blindly.
"""

    def run(self, user_text: str, emit: Callable[[AgentEvent], None] | None = None) -> str:
        fast = execute_fast_mission(self, user_text, emit=emit)
        if fast is not None:
            return fast
        return super().run(user_text, emit=emit)
