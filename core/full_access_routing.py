from __future__ import annotations

_BROWSER_SIGNALS = (
    "browser", "chrome", "google", "website", "web page", "http://", "https://", "new tab", "another tab", "search for",
    "متصفح", "كروم", "جوجل", "موقع", "صفحة", "تبويب", "ابحث", "بحث",
)
_FULL_TOOL_SIGNALS = (
    " git", "git ", "github", "repo", "repository", "code", "coding", "vscode", "visual studio code",
    "file", "folder", "directory", "terminal", "powershell", "command", "script", "npm ", "cargo ", "database", "supabase",
    "ملف", "مجلد", "كود", "جيت", "قاعدة بيانات",
)


def browser_focused_goal(user_text: str) -> bool:
    lowered = str(user_text or "").casefold()
    return any(signal in lowered for signal in _BROWSER_SIGNALS)


def needs_full_toolset(user_text: str) -> bool:
    lowered = " " + str(user_text or "").casefold() + " "
    return any(signal in lowered for signal in _FULL_TOOL_SIGNALS)
