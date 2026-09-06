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

## Status

The foundation now includes Claude tool calling, Windows automation, UI inspection, dialog control, specialized skills, persistent memory, task orchestration, recovery handling, and durable traces. Voice, remote control, broader device agents, and deeper service integrations remain modular next-stage capabilities.
