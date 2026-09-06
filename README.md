# JARVIS

A Windows-first desktop AI agent powered by Claude.

## Goals

- Natural-language desktop control
- Claude tool calling with an explicit local execution layer
- Permission-aware actions
- Windows terminal, process, filesystem, browser, dialog, UI Automation, and screen automation
- Persistent local memory
- Extensible skills and tool registry
- Task orchestration with recovery and durable execution traces
- Desktop UI that exposes agent activity and tool execution
- Optional control of the user's real Chrome session through Chrome DevTools Protocol (CDP)

## Architecture

```text
PySide6 Desktop UI
        |
        v
  High-Level Agent
        |
        +---- Task Orchestrator
        |       +-- task state
        |       +-- failure recovery
        |       +-- execution traces
        |
        +---- Claude / tool calling
        |
        +---- Skills
        |       +-- desktop-operator
        |       +-- browser-operator
        |       +-- developer
        |       +-- researcher
        |       +-- workspace skills
        |
        +---- Permission Engine
        |
        +---- Tool Registry
        |       +-- terminal
        |       +-- filesystem
        |       +-- windows / UIA
        |       +-- Win32 dialogs
        |       +-- browser
        |       |    +-- Playwright fallback browser
        |       |    +-- real Chrome CDP session
        |       +-- screen / input
        |       +-- Git / VS Code
        |
        +---- SQLite Memory
        |
        +---- .jarvis/traces
```

## Advanced behavior

JARVIS treats each user request as a tracked task with an identifier, turn count, tool history, failure count, recovery count, elapsed time, final status, and a durable JSON trace. Failed tool calls produce recovery guidance rather than blind repetition.

JARVIS also supports specialized Skills. Skills are procedural guidance layered above permissions, so adding a new skill does not bypass the safety model.

For Windows dialogs, the agent has direct Win32 inspection and control for common Save/Open flows. For Notepad persistence, `notepad_save_as` reads the live editor state and verifies the target file after writing it.

### Real Chrome session

JARVIS can optionally attach to an already-running Chrome instance through Chrome DevTools Protocol. The CDP tools expose the real browser's existing tabs and session state, and the selected real tab becomes the target for the existing `browser_*` tools.

Set `JARVIS_CHROME_CDP_URL` in `.env` when the debugging endpoint is not the default `http://127.0.0.1:9222`.

Chrome must be started with a DevTools Protocol debugging port for this mode. JARVIS does not attempt to bypass Chrome's security boundary or silently attach to a browser that has not exposed CDP.

Useful tools:

- `chrome_connect_cdp` — attach to the real Chrome session
- `chrome_tabs` — list available real tabs
- `chrome_use_tab` — select the tab used by `browser_*`
- `chrome_current_tab` — verify the selected tab

## Setup

1. Install Python 3.11+.
2. Create and activate a virtual environment.
3. Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Copy `.env.example` to `.env` and configure your provider credentials.
5. Start JARVIS:

```powershell
python -m app
```

## Safety model

JARVIS does not give the model unrestricted operating-system access. Every action is mapped to a named local tool and checked by the centralized permission engine. High-risk actions are blocked unless the configured policy explicitly permits them.

Browser human-verification challenges are detected before interactive browser actions. JARVIS does not solve, bypass, or automate CAPTCHA/anti-bot controls; it stops at that boundary and can continue after the user completes the human-verification step.

## Status

The foundation now includes Claude tool calling, Windows automation, UI inspection, dialog control, specialized skills, persistent memory, task orchestration, recovery handling, durable traces, browser challenge protection, and optional real-Chrome CDP integration. Voice, remote control, broader device agents, and deeper service integrations remain modular next-stage capabilities.
