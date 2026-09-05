# JARVIS

A Windows-first desktop AI agent powered by Claude.

## Goals

- Natural-language desktop control
- Claude tool calling with an explicit local execution layer
- Permission-aware actions
- Windows terminal, process, filesystem, browser, and screen automation
- Persistent local memory
- Extensible tool registry for VS Code, GitHub, Supabase, and other devices
- Desktop UI that exposes agent activity and tool execution

## Architecture

```text
PySide6 Desktop UI
        |
        v
    Agent Core
        |
        +---- Claude API / tool calling
        |
        +---- Permission Engine
        |
        +---- Tool Registry
                 +-- terminal
                 +-- filesystem
                 +-- windows
                 +-- browser
                 +-- screen
                 +-- automation
        |
        +---- SQLite Memory
```

## Setup

1. Install Python 3.11+.
2. Create and activate a virtual environment.
3. Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Copy `.env.example` to `.env` and add your Anthropic API key.
5. Start Jarvis:

```powershell
python -m app
```

## Safety model

Jarvis does not give Claude unrestricted operating-system access. Every action is mapped to a named local tool. High-risk tools are denied unless explicitly approved by the permission layer.

## Status

This repository starts with the functional agent foundation. Device agents, remote control, voice, and deeper integrations are designed as subsequent modules rather than being hard-wired into the core.
