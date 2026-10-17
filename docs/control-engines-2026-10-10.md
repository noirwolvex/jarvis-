# Coordinated control engines — implementation and qualification

## Architecture assessment

JARVIS already has a Python execution core, a PySide6 desktop entry point, a
Next.js/React Control Center, TypeScript contracts/runtime, a Rust native daemon,
and a PostgreSQL/Prisma foundation. The hybrid Control Center uses
`core/full_access_bridge.py` to assemble the Python agent and its tools. The
separate TypeScript simulation is retained.

The useful control stack was already present:

| Responsibility | Existing implementation reused |
| --- | --- |
| Planning and fast command compilation | `fast_execution_agent.py`, mission compilers, `full_access_agent.py` |
| Tool schemas and authorization | `tools.py`, `permissions.py`, `action_confirmation.py`, `mission_control.py` |
| Durable ordered execution and recovery | `orchestrator.py`, `workflow_tools.py`, task checkpoints |
| DOM and Windows semantic interaction | `universal_interaction.py`, `semantic_ui_tools.py`, `semantic_target.py`, browser/CDP adapters |
| Native input | `rust_engine.py`, Rust daemon, `native_ui_input.py`, guarded Python fallback |
| Capture and perception | `vision_tools.py`, `computer_perception.py`, `interaction_scene.py` |
| Launching and folders | `app_tools.py`, `known_folder_tools.py` |

`pywinauto` and `PyAutoGUI` already belong to the base requirements. Windows UI
Automation is used through pywinauto's UIA backend. Rust remains the preferred
native input backend. Adding another independent desktop controller would have
duplicated policy, foreground checks and recovery.

The implemented extension supplies shared typed requests/results, five concrete
adapters, a process-wide input scheduler, cooperative action deadlines, scoped
cleanup, local OCR and coordinate utilities. It retains the existing planner,
browser-session selection, native guards, confirmation UI and durable workflows.

## Implementation map

All new engine code is in `core/control_engines/`:

| Module | Responsibility |
| --- | --- |
| `models.py` | Validated Action, Check, RetryPolicy, CancellationToken, Result and Status |
| `engines.py` | MouseEngine, KeyboardEngine, SmartUIEngine, VisionEngine, VerificationEngine and AgentProvider |
| `scheduler.py` | Shared input lane, nested deadlines, cancellation context |
| `session.py` | Ordered execution, dependency checks, immutable IDs, evidence, read-only recovery, partial reports |
| `geometry.py` | Physical pixels, negative display origins, scaling, window/image transforms, margins, target freshness |
| `vision.py` | Pluggable OCR provider, Windows OCR, ROI capture, text boxes, change comparison, bounded exact templates |
| `integration.py` | Registers `workflow_control_action` and `vision_ocr` on the existing hybrid agent |

The dispatch chain is:

```text
Existing mission planner / deterministic compiler
  -> typed action or existing durable workflow
  -> AgentProvider / existing child dispatcher
  -> authorization + one-time confirmation + foreground/scene guards
  -> shared input lane
  -> existing semantic DOM/UIA or guarded native backend
  -> independent readback + result + existing mission trace
```

The five engines expose these operation names. Parameters are the existing
registered tool schemas, not a second incompatible set of selector rules.

| Engine | Typed operations |
| --- | --- |
| Mouse | `click`, `move`, `click_coordinates`, `double_click`, `drag`, `scroll`, `cursor`, `mouse_down`, `mouse_up` |
| Keyboard | `type_text`, `hotkey`, `press_key`, `key_down`, `key_up` |
| Smart UI | `inspect`, `resolve`, `focus`, `launch`, `open_folder`, `navigate` |
| Vision | `observe`, `capture`, `ocr` |
| Verification | `verify`, plus read-only preconditions and postconditions |

Semantic clicks/text/focus/scroll use the universal interaction router. Existing
UIA identity, ambiguity, enabled/visible state and stale-reference checks remain
in effect. Explicit raw input still needs the existing fresh scene/foreground
guards. Geometry helpers are computations, not permission to dispatch input.
Window discovery, resizing/positioning where supported, and other existing tools
remain available through their original interfaces.

## Execution contract

- An action has an immutable ID, operation, target, parameters, timeout,
  preconditions, expected outcome, read retry policy and dependencies. Parameters
  cannot overwrite target fields. The entire tool schema is validated before input.
- Caller/model JSON cannot assign risk, disable confirmation or supply executable
  Python/shell code. Risk and permission come from the registered child tool.
- One ControlSession accepts one outstanding action. A process-wide reentrant
  lane also serializes legacy registry callers. It covers state checks and input
  dispatch; independent background computation can still run separately.
- Timeouts are 0.05–120 seconds, and inherit a shorter parent deadline. Cancellation
  is propagated to existing cooperative polling and input adapters. A timed-out
  provider retains its lane until it returns; the session refuses conflicting work.
- RetryPolicy retries **read-only checks**, at most five attempts with bounded
  backoff. A delivered mutation is never blindly repeated. Uncertain outcomes stop
  the sequence and report the remaining action IDs.
- Each typed dispatch releases only the synthetic holds it introduced, on its own
  input worker while still holding the lane. It does not release another action's
  keys during an unrelated read or from a stale timed-out worker.
- Atomic hotkeys and drags are the supported way to hold input across a sequence.
  Strict Rust intentionally rejects detached key/button holds. In the Python
  fallback, a typed standalone down operation is scoped to its dispatch and cleaned
  up immediately; it cannot keep a modifier held across model turns.
- `SUCCEEDED` requires a successful read or a backend's verified postcondition.
  Raw delivery alone is `DELIVERED`. An explicit postcondition must return positive
  independent evidence; it can invalidate an earlier backend success. Empty reads
  and unverified verification results cannot complete an action.
- Reusing an ID returns its stored outcome without replay; changing its arguments
  is rejected. Session ID history is in memory, bounded to 500 actions. Use existing
  `workflow_execute`, `workflow_status` and `workflow_resume` for persistent mission
  programs. Their child traces, write-intent journal and uncertainty review are reused.

Results contain timestamps, duration, status, target, observed state, verification,
retry count, error code/message and evidence with actual backend telemetry.
The `jarvis.control` logger records metadata only. The existing mission trace and
event stream carry child results to the Control Center. An optional `on_event`
callback receives structured session progress. Existing pause/resume/cancel and
voice/status feedback are preserved.

## Python API example

Use an existing configured hybrid agent with an active mission. Resolve the target
from a fresh inspection first. This example assumes the observed browser has a
unique textbox named `Draft`:

```python
from core.control_engines import (
    Action, AgentProvider, CancellationToken, Check, ControlSession, RetryPolicy,
)

target = {"surface": "browser",
          "browser_target": {"role": "textbox", "name": "Draft"}}
text = "Hello مرحبا\nSecond line"
cancel = CancellationToken()
with ControlSession(AgentProvider(agent)) as controls:
    result = controls.execute(Action(
        action_id="write_draft_1",
        action_type="type_text",
        target=target,
        parameters={"text": text, "replace": True, "submit": False},
        timeout=10,
        preconditions=(Check("interaction_wait", {**target, "state": "visible"}),),
        expected_outcome=Check("interaction_wait", {
            **target, "state": "text", "text": text,
        }),
        retry_policy=RetryPolicy(read_attempts=2, backoff_seconds=0.05),
    ), cancel)
    print(result.as_dict())
# Another thread may call cancel.cancel() while an action is running.
```

The model-facing equivalent is `workflow_control_action({"action": {...}})` with
the same JSON fields; cancellation tokens remain trusted runtime objects. For known
multi-step work, use the existing durable workflow container to avoid model calls
between deterministic actions. No new LLM calls were added to the execution path.

## Local vision setup and boundaries

Base capture/semantic control continues to use `requirements.txt`. Local Windows
OCR is optional:

```powershell
rtk proxy python -m pip install -r requirements-ocr.txt
rtk proxy python -m scripts.control_engine_qualification
```

`requirements-ocr.txt` adds PyWinRT projections for Windows OCR and its image/buffer
types. No cloud service, Tesseract binary or OpenCV installation is required.
The requested OCR language must already be available in Windows. The tool reports
missing bindings/languages without silently downloading language packs or changing
system settings. It was exercised with Python 3.14 and English OCR on this machine.

`vision_ocr` accepts window (default), desktop or explicit physical ROI scope. It
uses a fresh stable local capture, checks the frame hash, maps resized image boxes
to physical desktop coordinates and checks foreground/window geometry again after
OCR. Images stay local in this provider. Existing configured visual providers are
unchanged.

Windows OCR provides text and boxes but no confidence score: results use `null`.
The engine does not fabricate confidence or turn OCR text into blind clicks. Target
validation checks age, window, observation ID and confidence. Fresh semantic
resolution or a newly guarded visual observation is required for consequential
input. Same-window content can change while OCR runs; a returned box alone never
authorizes a later click. OCR cannot identify every icon, canvas control or disabled
state. Exact template matching is an opt-in utility for small, unscaled assets;
its search budget prevents expensive full-screen brute-force matching.

Reference implementation details: [PyWinRT buffer/async projections](https://pywinrt.readthedocs.io/en/stable/types.html)
and [Windows OCR recognition](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.recognizeasync).

## Reproducible validation

```powershell
rtk proxy python -m pytest -q
rtk proxy python -m scripts.verify_browser_control
rtk proxy python -m scripts.control_engine_qualification
rtk proxy python -m scripts.build_control_fixture
rtk proxy git -c core.safecrlf=false diff --check
```

Use the project's installed Python interpreter if the Windows `python` alias does
not resolve. The browser qualification uses an isolated headless Chrome profile
and a loopback fixture. It does not type into personal accounts or send messages.
The desktop builder compiles, but does not launch,
`.jarvis/qualification/ControlEngineFixture.exe` using the installed .NET compiler.
Open that owned window, focus `Fixture draft`, enter test text and press
`Apply fixture` once. Verify the visible state and the independent
`.jarvis/qualification/desktop-engine-fixture.json` report, then close the window.

Validation performed in this workspace on October 10:

- Final full Python suite: **1,046 tests and 570 subtests passed** (32.21 seconds).
  The new engine/vision suite contributes 35 tests and 10 subtests.
- All JavaScript/TypeScript workspace tests passed: **77 tests** (50 Control Center,
  21 runtime, 6 database). All workspace type checks and `git diff --check` passed.
- Typed browser qualification: five verified actions (inspect, focus, bilingual
  typing, button, navigation), zero model calls, zero input replays on repeated
  IDs, one scoped fixture confirmation, missing-target blocking and cancellation.
  Final measured run: **331.72 ms**, including recovery/cancellation checks.
  The existing eleven-step deterministic workflow passed in **632.69 ms**.
- Existing isolated browser scenarios also passed: multiline and Unicode input,
  semantic buttons, selection, shadow DOM, dialogs, loading, stale target IDs,
  iframe controls, navigation, challenge guard and explicit tab creation.
- Actual local English OCR recognized a generated image and returned a text box.
  It passed on the main thread (53.26 ms) and a background worker (201.0 ms).
- Owned Windows fixture: launched and activated, field discovered through UI
  metadata, text `JARVIS control — مرحبا بالعالم` entered and read back exactly,
  Apply clicked once, application report matched exact text and click count 1,
  and the window closed. A first focus attempt lacked screenshot geometry; fresh
  observation resolved it before a single retry. These desktop inputs used the
  available Computer Use test harness, **not JARVIS's Rust injector**.
- Unit tests cover all five adapters, permission/confirmation boundaries, verified
  completion, no mutation retries, cancellation/deadlines, late provider quarantine,
  shared scheduling, scoped release, Unicode data, geometry, OCR boxes and templates.

Fixture timings describe isolated deterministic tests, not arbitrary app launch,
network navigation or model planning performance. The typed JARVIS/agent/CDP path
was exercised end to end. Native Rust injection, clipboard fallback, mixed-DPI
hardware and every third-party application were not requalified end to end in this
change. Existing tests cover those adapters' dispatch contracts. Python cannot
forcibly terminate a hung in-process COM/native call safely; quarantine prevents
conflicting input while it drains. These are explicit qualification limits.
