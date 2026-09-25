# JARVIS

A Windows-first desktop AI agent powered by Claude.

## Autonomous desktop upgrade

See the [September 25 Discord navigation repair](docs/discord-navigation-2026-09-25.md) for the first-chat command completed live in 4.157 seconds with no model calls, using scoped conversation links and semantic verification.

See the [September 25 end-to-end typing repair](docs/typing-mission-2026-09-25.md) for the verified dashboard → Python → Rust WhatsApp draft test, caret-provider fix, read-only recovery, and exact completion checks.

See the [September 24 device-control repair](docs/device-control-2026-09-24.md) for the WhatsApp search/composer collision, faster semantic resolution, native click guards, bounded recovery and current validation. The [earlier typing repair](docs/typing-fix-2026-09-23.md) covers duplicate UIA identities and worker replacement. The [September 23 control execution report](docs/control-execution-2026-09-23.md) covers native input and workflow recovery; the [September 21 execution review](docs/project-review-2026-09-21.md) records the earlier checkpoint improvement and branch review.

The [mouse and keyboard performance follow-up](docs/input-performance-2026-09-21.md) adds guarded click delivery, physical-window hit checks, fewer redundant movements and UIA reads, with isolated benchmark results and activation notes.

The hybrid control center now connects the existing Python agent to guarded screen, mouse, keyboard, browser, application, file, Git and terminal tools. It adds atomic task checkpoints, recovery recall, action-review gates, live progress and desktop previews, and a Full Access emergency-stop latch with **Ctrl+Alt+Escape**. Terminal commands require an additional explicit session permission.

Full Access also includes ordered semantic workflows: compile known steps once, execute through the existing permission gates, verify meaningful checkpoints, and recover without replaying completed actions. Bounded DOM/UIA snapshots, cancellable state waits, Discord conversation checks and verified YouTube playback reduce repeated model calls and screenshots. See the runbook for measured fixture results and live-testing limits.

Authorized dashboard missions also sample changed desktop previews in the background and reuse the latest image at existing model decisions. Simple supported Arabic commands take a direct verified path without model calls. Pointer movement, dragging and scrolling check cancellation and focus during execution. Live previews stop with the mission and never replace the stable observation required for coordinate input.

See the [desktop-agent runbook and validation limits](docs/autonomous-desktop-upgrade.md) for setup, controls, recovery, tests, and the remaining native qualification requirements. Full Access starts disabled in a new server session; simulation remains the default control mode.

## JARVIS X reference implementation

The repository also includes a separate Next.js/TypeScript/Rust foundation for the expanded JARVIS X specification. The existing Python application below remains available. By default, the control center runs a **deterministic, in-memory workspace simulation**. Set `JARVIS_CONTROL_MODE=hybrid` to use the Python desktop agent described above.

```powershell
rtk proxy npm.cmd ci
rtk proxy npm.cmd run db:generate
rtk proxy npm.cmd run dev
```

Open [Mission Control](http://127.0.0.1:3000). Use **Run simulation** to follow four verified actions through the task graph, timeline and memory. Ctrl+K opens the command palette. The UI includes pause/resume, a simulation stop latch, JSON event export, dark/light themes and responsive monitoring. Node 22.12+ is required; use `npm` in non-Windows shells.

Run one managed instance at a time. Stop the terminal's existing `npm run dev` or `npm start` with **Ctrl+C** before switching modes. The launcher checks for an occupied port before changing Rust and blocks duplicate launches, including `verify:rust-live`. It starts Next.js directly through Node, so executable paths with spaces do not pass through a batch shell. See the [startup repair and validation](docs/runtime-startup-2026-09-25.md).

- [Complete 28-section engineering architecture](docs/architecture.md): topology, trust boundaries, all matrices, resource budgets, security and deployment decisions.
- [Implementation map and limitations](docs/requirements-traceability.md).
- [TypeScript and control-center runbook](docs/typescript-implementation.md).
- [Rust mTLS daemon and platform runbook](docs/rust-implementation.md).
- [PostgreSQL schema and migration runbook](database/README.md).
- [Stage 0–13 acceptance roadmap](docs/implementation-roadmap.md).

Validate with `npm test`, `npm run typecheck`, `npm run build`, `npm run db:validate`, and `cargo test --manifest-path daemon/rust/Cargo.toml`. Use `npm run demo` for a headless simulation. CI definitions cover the TypeScript build/tests and Rust portable tests on Windows/Linux. See [validation evidence](docs/validation.md) for checks actually executed in this workspace.

This is a tested **reference foundation**, not a production release. The browser simulation, standalone WebSocket gateway, Rust daemon and Prisma storage are distinct surfaces. Durable orchestration integration, native target binding, hostile-plugin/OS sandboxing, real model providers and hardware kill-switch qualification remain explicit roadmap work.

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
