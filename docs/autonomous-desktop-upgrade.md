# Autonomous desktop upgrade

The desktop execution path is the existing Python agent, reached through the local Next.js hybrid control center. Browser/CDP adapters, UI Automation inspection, application discovery, workspace tools and model configuration are reused. The TypeScript simulation and Rust daemon retain their separate execution paths.

## Start and authorize

Use Python 3.11 or newer and Node 22.12 or newer on Windows. Install the updated Python requirements, including `jsonschema`, and the npm dependencies. Configure the existing model-provider settings and an explicit `JARVIS_WORKSPACE` in the local environment. A vision-capable model is required to interpret screen images.

From the repository, in PowerShell:

```powershell
rtk proxy python -m pip install -r requirements.txt
rtk proxy npm.cmd ci
$env:JARVIS_CONTROL_MODE = 'hybrid'
rtk proxy npm.cmd run dev
```

Open the loopback control center at `http://127.0.0.1:3000`. A new server session starts in Standard mode. Choose **Enable Full Access** and confirm the displayed scope to authorize desktop missions. Screen images may be sent to the configured model provider. The optional terminal checkbox explicitly permits PowerShell commands for that session. Those commands run as the current Windows account.

Use the existing environment variables `JARVIS_REPO_ROOT` and `JARVIS_PYTHON_EXECUTABLE` when the server cannot locate the checkout or the intended Python interpreter. The checkout and Python installation must remain available at runtime; the Python agent is not packaged inside Next.js output. The page selects its mode at request time, so production startup can select hybrid mode after building.

The persistent worker is now required for dashboard missions. The old `JARVIS_FULL_ACCESS_PERSISTENT_WORKER=false` switch no longer selects an uninterruptible one-shot dashboard path. Existing direct Python entry points remain available, but the dashboard worker provides the integrated hotkey and stop protocol. An outdated idle worker is replaced before accepting another mission.

## Controls and boundaries

- **Emergency stop** immediately latches the server state, revokes Full Access and terminal permission, and sends cancellation independently of the model request. A separate worker thread handles stop input. Ctrl+Alt+Escape is polled every 20 ms while the worker is alive. Held inputs are released and tracked command trees receive termination requests. Node requests forced worker termination after 300 ms if the worker has not exited.
- **Reset emergency stop** becomes available after active execution settles. Reset leaves Full Access disabled. Enable it explicitly again before another unrestricted desktop mission.
- **Disable Full Access** stops an active mission and revokes access. An idle worker is also stopped, so its keyboard watcher does not remain active after disable.
- Local HTTP requests require the loopback host and same-origin checks. Mutations require the control header and bounded JSON bodies. Access changes use an explicit confirmation value; enabling terminal execution uses a distinct confirmation.
- The permission engine validates the schema before dispatch. Explicit denied tools remain denied. Restricted mode rejects raw desktop actions, browser mutations, Git writes and filesystem writes. Direct terminal execution requires the extra terminal grant; other HIGH/CRITICAL tools remain separately gated.
- Full Access is authority to operate the current interactive account. Workspace boundaries apply to the dedicated file tools. GUI interactions and explicitly enabled terminal commands have the account's wider permissions. This is not an OS sandbox or a security boundary against arbitrary code, malicious repository hooks, or privileged software.
- CAPTCHA and browser human-verification checkpoints still pause the task and leave the page intact. Dialog input in foreground Chrome also passes through the browser challenge guard.

## Perception and precise input

During an authorized dashboard mission, a background monitor samples the desktop with a 500 ms target interval and publishes only changed previews. It holds one latest JPEG in memory, bounded to 960 pixels on the longest edge and 750 KB, without a disk queue. The dashboard polls state once per second and labels the timestamp as the latest screen change; it does not flood the event log with preview updates. This is sampled live context, not a video stream or a hard real-time guarantee.

At an existing model decision, the agent can attach the newest preview without adding a model request. An explicitly requested `screen_observe` image takes priority for the next decision so its coordinate mapping remains available. Previews older than three seconds without a successful sample, failed captures, revoked permissions and stopped missions cannot provide new model context. Captures stop when the mission finishes or pauses, Full Access is disabled, or emergency stop is activated. An explicit `screen_observe` deny rule also disables the monitor. Set `JARVIS_LIVE_PREVIEW=false` to retain on-demand capture only. Capture counts, changes and errors are recorded in execution metrics. This background path does not independently interpret every frame or authorize input.

`screen_observe` captures the Windows virtual desktop, including negative monitor origins and the source-to-preview scale. It checks the foreground during capture, attempts to settle changing UI for a bounded period, and publishes the latest JPEG to the model and dashboard. The default settling budget is 300 ms; captures stop early when sampled images agree. JPEGs are bounded to 1.5 MB and the newest eight captures are retained in `.jarvis/vision`.

Raw desktop input requires a stable, foreground-bound observation and coordinates within its captured virtual bounds. Immediately before dispatch, another screen sample is compared with the retained scene signature. A changed foreground or scene requires observation again. Scene-bound evidence expires after 60 seconds; this accommodates provider latency while still checking the current scene before input. The comparison uses a reduced image and tolerances, so it is a guard against visible changes rather than proof of element identity. Semantic browser and UI Automation tools remain the preferred route.

Every raw desktop mutation consumes its observation. After successful input, the agent captures the resulting screen and returns to the model before executing another queued visual action. Deferred tool calls receive explicit results asking for a revised action after inspecting the new screen.

Windows Unicode input now uses the complete native `INPUT` union: 40 bytes on 64-bit Windows and 28 on 32-bit Windows. Non-BMP characters use UTF-16 surrogate pairs. Newlines generate one return sequence. Partial input and uncertain UIA writes fail without replaying text through the clipboard. Verification reads the editor instead of writing the text again. Synthetic shortcuts track held keys for cleanup, including fail-safe-corner cleanup.

The Python pointer path uses smooth interpolation with cancellation and foreground checks between samples, without PyAutoGUI's additional per-call pause. Dragging retains one foreground binding and releases its tracked button on failure. Wheel input is delivered in bounded chunks with cancellation/focus checks. These changes preserve the existing raw-input observation gate and PyAutoGUI fail-safe; they do not bypass verification or apply new behavior to the separate Rust input backend.

## Planning, verification and recovery

### High-speed semantic execution

The Full Access factory now registers `workflow_execute`, `workflow_status`, `workflow_review`, `browser_semantic_snapshot`, `browser_semantic_action`, `browser_wait_state`, and `ui_wait_state` on the existing `FastExecutionFullAccessAgent`. This is the same agent used by the persistent dashboard worker. Worker revision 4 replaces an outdated idle worker before the next mission; an active older worker must finish or stop first.

For an unfamiliar interface, the model obtains a compact semantic snapshot, chooses exact targets, and submits known steps as one ordered workflow. Browser actions use DOM/CDP; native applications use UI Automation; existing application/file/Git/native tools remain available. Raw coordinates remain on the existing screen-bound path and are deliberately excluded from deterministic workflows.

`workflow_execute` accepts up to 32 steps per call. Every step has an ID, description, tool and arguments, plus an optional read-only semantic checkpoint. The entire program is schema- and permission-checked before the first action; each child still goes through the normal Full Access dispatcher. There is no additional model request between known steps. Compound UI operations also enforce their nested tool policies. Full Access, terminal permission, Rust overlays, foreground validation and emergency cancellation remain in force.

Each workflow stores its immutable program and per-step journal in the task checkpoint. Repeating the same workflow ID resumes only unfinished work. A delivered action with a pending checkpoint retries only the observation. An uncertain send, write or click is never automatically replayed: inspect the live outcome, record a fresh `task_verify` claim, then use `workflow_review` before continuing. `workflow_status` restores program context after chat trimming; metadata alone cannot verify an action. Workflow budgets are 20 programs, 100 steps and 256 KiB of program data per mission.

The narrow text compiler still handles unambiguous app-opening and Google-search chains without a model request. Unknown action clauses fall back to the model instead of being swallowed into a search query. A fast-path failure now continues the same task through model recovery, retaining completed steps and the original browser baseline. Full Access plans preserve required steps and their order; unfinished workflows or skipped steps cannot produce mission completion.

The direct compiler also recognizes strict Arabic app-launch chains such as `افتح ديسكورد ثم افتح الحاسبة ثم افتح المفكرة`, quoted searches such as `ابحث في جوجل عن "تعلم بايثون"`, quoted playback requests such as `شغل "اسم الأغنية" على يوتيوب`, and explicit current-video play/pause commands. Every clause must match; an unrecognized requested step sends the whole mission to the model. Successful deterministic commands need no model call, while their existing semantic tools still verify results. This is a bounded command grammar, not unrestricted Arabic language understanding without a model.

### Observation reuse and checkpoint behavior

- Browser snapshots include visible semantic controls, hierarchy, focus, dialogs, page state, viewport and explicit iframe context. A per-document mutation/focus/layout version invalidates observed node references. Detached snapshots are reused for up to 500 ms when their version remains valid. Closed shadow DOM is not exposed; iframe inspection needs an explicit frame selector.
- UIA snapshots include bounded controls, parent context, focus and window/process/framework metadata. Detached read metadata is cached for up to 250 ms; JARVIS mutations invalidate the affected window. External native UI changes are bounded by that TTL or `force_refresh`, rather than a system-wide UIA event subscription. Actions always resolve live controls and never act on cached wrappers.
- Ready states return immediately. Loading states use cancellable polling rather than fixed post-action sleeps. Missing/ambiguous controls do not trigger blind clicks. Browser commands abandoned while queued are cancelled before dispatch; an already-delivered action cannot be undone.
- Native activation and shortcuts report delivery rather than success. A semantic assertion verifies the intended result before another dependent mutation. `ui_batch` performs those checks inside one call. Text entry requires exact readback; submission requires new post-submit evidence. Semantic paths do not automatically capture screenshots.
- Tracked keyboard and mouse down/up primitives disable only PyAutoGUI's per-call pause. Fail-safe checks, input tracking and cancellation remain active; the global pause setting is unchanged.

### Reusable application operations

Discord navigation resolves an exact server/channel/DM and verifies selected context and its composer. The Quick Switcher is used only when direct semantic navigation is unavailable. An optional `server` disambiguates channel names. `discord_navigate_and_send` carries the verified context through one explicitly requested send, refuses drafts and never retries uncertain submission. Unsupported layouts, missing UIA selection/value/runtime identities and unsupported labels require fresh inspection; the current specialized label matching is English-oriented. Composer clearing/new visible message evidence is a local UI postcondition, not proof of server delivery or recipient receipt.

`youtube_search_open(query, new_tab=false, play=true)` searches, opens the first resolved watch result, checks its video ID and verifies advancing content playback in one call. `youtube_playback` supports status, play and pause. Playback checks require matching selected/loaded IDs, and reject ads, blocked autoplay or unavailable evidence. The first result is not a guarantee of relevance to every natural-language music request; the model must assess the returned title/identity when the request requires a specific rendition. CAPTCHA checks run before and after browser mutations and fail closed.

Existing VS Code, Git, file and Windows application tools are reusable workflow steps. A tool that only reports delivery needs an explicit semantic checkpoint; the workflow does not turn a launch request into verified application success.

Plans have unique step identifiers, known dependencies and no cycles. Starting or completing a step requires its dependencies to finish. Plan size is bounded to 100 entries in the orchestrator; the model-facing plan tool currently exposes a 20-step plan, which can be revised as a mission progresses.

An intent checkpoint is flushed and atomically replaced before dispatch. Tool results, plan changes and verification records also update the checkpoint. If a process exits between intent and result, the action remains uncertain. Cancellation preserves that uncertainty. Checkpoints live in `.jarvis/traces/task-*.json`; known token and password patterns are redacted before persistence. This redaction is best effort and does not make arbitrary user content public or safe to share.

`task_recall` retrieves a named or latest previous checkpoint without executing it. For a continuation request, the agent is instructed to recall the checkpoint, re-observe the live state, and plan the remaining work. Old evidence never authorizes automatic replay of an uncertain write or click. Checkpoint restoration does not restore permissions or a live browser connection.

An unverified mutation blocks another mutation until successful observation and an explicit `task_verify` review exist. Tools with a verified semantic postcondition can record that evidence directly. Cursor arrival alone does not verify an application outcome. A failed claim can be reviewed as false, recovered, and re-verified under the same claim. New mutations invalidate older completion evidence. Model interpretation of a screenshot is still a model judgment, not an independently proven task outcome.

Three matching failed actions without intervening successful observation stop the loop. Commands with nonzero exit codes, browser challenge blocks and cancelled actions count as failures. The model context retains at most twelve complete assistant/tool groups, while durable task state and previous evidence remain available through the task tools.

Full Access defaults to 160 model turns, configurable with `JARVIS_FULL_ACCESS_MAX_TURNS` and capped at 512. Dashboard missions have a 30-minute deadline and accept objectives up to 8,000 characters. One mission executes at a time. Deadlines are bounded resource controls, not claims that arbitrary tasks finish within those limits; unfinished tasks can be recalled and continued.

## Application, file and command handling

Managed Chrome/CDP routing reuses selected tabs and honors explicit new-tab requests. Existing application discovery and UIA inspection are reused. Ambiguous window and dialog targets fail rather than selecting an arbitrary first match. Save-dialog closure and file existence no longer claim that file contents were verified.

VS Code launches reuse a window and resolve the native executable behind Windows' `code.cmd` wrapper. The resulting window must still be inspected. Git remote/ref arguments reject option-shaped values. Git and PowerShell commands use cancellable process tracking, bounded retained output, time limits, and nonzero-exit detection.

On Windows, each command starts suspended, is assigned to an unnamed Job Object, and resumes only after assignment succeeds. The job terminates descendants on cancellation or handle closure, including forced worker death. Normal command completion also closes the job, so background children cannot retain output pipes indefinitely. Assignment failure terminates the suspended command without running it. These lifecycle rules follow [Microsoft's Job Object documentation](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects). Jobs control process lifetime; they do not sandbox filesystem/network access or roll back an already-applied system change. A worker crash in the small create-to-assign interval can still leave a suspended process for operator cleanup.

Workspace file reads/writes remain available. New directory creation and exclusive file copying verify their outcomes; copying checks SHA-256 and refuses to overwrite a destination. Copy size is capped at 16 MiB. Arbitrary filesystem and system changes can be performed through explicitly enabled terminal access under the current account, subject to the task instruction and tool policy.

## Validation and remaining qualification

The upgrade includes tests for dependency cycles, crash checkpoints, continuation recall, evidence requirements, repeated failures, stale/changed scenes, coordinate bounds, Windows input layout, Unicode surrogate handling, partial-delivery rejection, schema rejection, restricted permissions, worker cancellation, command timeout, Git option rejection, dialog ambiguity and verified file copying. Windows Job Object tests launch real disposable command trees and verify descendant termination after timeout and forced worker death, plus failure before command execution when assignment is rejected. Screen tests use synthetic images; control-center tests use fixture workers.

The control-center browser inspection confirmed the rendered hybrid interface, the real emergency controls and the permission descriptions. It did not enable additional device permissions or execute a live model-driven desktop mission. Existing Python, TypeScript, database and Rust regressions are checked alongside the new cases. CI is configured to install Python test dependencies and run Windows and Linux coverage; Windows-specific cases are explicitly skipped on Linux. The CI matrix itself was not executed locally.

Local Windows validation updated on September 16, 2026:

| Check | Result |
| --- | --- |
| Python unit and integration suite | 239 passed |
| Control-center tests | 13 passed |
| TypeScript runtime tests | 21 passed |
| Database migration/invariant tests | 6 passed, including the parent test |
| Rust tests | 20 passed; 3 ignored |
| Workspace TypeScript checks | Passed |
| Next.js production build | Passed |
| Python source compilation | Passed |
| Native Rust adapters | `cargo check --features native` passed |
| Prisma schema validation | Passed |
| npm dependency audit | 0 vulnerabilities |

Performance regressions assert structural costs rather than unreliable wall-clock promises: eight verified workflow actions complete in one model tool-call cycle plus the final response (two model calls total), with no requested intermediate screenshots; sixteen direct workflow steps use no model calls, and repeating that completed program dispatches no actions. The dashboard's optional live monitor adds sampled frames independently; a separate regression checks that its latest image adds no model round-trips and does not accumulate images in context. Ready-state waits do not sleep, repeated unchanged snapshots hit their caches, and checkpoint retries do not repeat the preceding mutation. Tests also cover permission-gated capture, capture failure, mission failure, cancellation, stale previews, explicit coordinate-image priority, focus loss and button release. These are fixture measurements, not production latency benchmarks.

Browser tests execute isolated headless Chromium against local HTML fixtures, with network requests intercepted. They cover real DOM targeting, snapshot invalidation, loading waits, ambiguous controls, cancellation and simulated media properties. Discord/UIA tests use controlled fixtures. No live Discord messages or end-to-end provider-driven desktop missions were executed. CI now installs Playwright and Chromium to run the browser fixtures. Rust tests used `.jarvis/validation/rust-target` because the running daemon locked the normal output executable; the daemon was left running.

Production qualification still requires supervised native missions against representative apps, multi-monitor/DPI and keyboard layouts, loading animations and unexpected popups, real provider latency, protected/elevated windows, browser disconnection, abrupt OS termination, and measured stop latency under load. Capture and hotkey timings above are configured polling budgets, not hard real-time guarantees. Full Access does not bypass Windows secure desktop/UAC or provide guaranteed completion of every complex task.
