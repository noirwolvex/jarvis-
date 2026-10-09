# App, mouse-to-keyboard, and typing speed — 9 October 2026

The speed changes reuse the existing deterministic compiler, app discovery, semantic typing, and Rust client. No daemon rebuild or permission expansion is required.

## Changes and evidence

| Path | Change | Evidence |
| --- | --- | --- |
| App resolution | Stop name discovery at a unique exact highest-priority Start Apps match; explicit paths skip name discovery. Cache successful resolutions for at most 60 seconds, bounded to 32 entries. | Read-only Notepad check on this device: full discovery 2302.97 ms, optimized first resolution 803.33 ms, cached resolution 0.02 ms. All selected the same installed entry. Single-run measurements, not a startup-time guarantee. |
| Existing executable apps | Reuse one visible window with an exact installed executable path; reread HWND/PID/process identity and foreground after activation. | Mocked existing-window test: zero launch attempts and zero polling delays. Ambiguous windows, path impostors, recycled identity, and focus loss are rejected. |
| Ordinary app commands | `please open Notepad`, `launch Notepad`, and `start Notepad` compile directly, including ordered supported clauses. | Compiler/execution tests confirm normal approval checks and step order. Unknown trailing work falls back intact; quoted typing content stays literal. |
| Browser typing | A literal typing command without a named browser target resolves the actually focused writable editor from one fresh snapshot. | Live Chrome fixture confirms exact text in the intended field, with zero model calls. Real-browser regressions reject absent focus and focus changes before dispatch. No implicit submit. |
| Native/semantic typing | Bind a working value provider once per action; reread its current value or a new document range at every guard and verification point. | Controlled TextPattern-only tests reduce failed provider queries from 9 to 3 for native typing and 12 to 4 for semantic typing. Fresh text reads remain 4 and 5 respectively. |
| Compatibility handoff | Reuse the resolved editor and value reader when a native backend is unavailable before dispatch. | Tests preserve generation, identity, and provider-failure rejection without repeating a UIA tree lookup. No replay after uncertain input. |
| Mouse → keyboard bursts | Seed the existing keyboard-frame cache from the fresh capture already required by a mouse action. | Client regression reduces immediate click→type capture calls from 2 to 1. Every mouse action still captures afresh; keyboard reuse expires after 750 ms or on HWND/PID/display change. Slow capture cannot extend that lifetime. |

Cached app metadata is invalidated by environment/cwd changes, missing executable/shortcut files, expiry, failed launch, or absence of launch evidence. Cached metadata never replaces foreground evidence or authorization. Store/shortcut entries do not use the new existing-window shortcut because their friendly names alone cannot prove process identity. The existing new-launch window-matching behavior was not redesigned in this speed pass.

Mouse paths still retain their existing short smooth duration and fresh target guards. The Rust Unicode implementation already batches input; this pass removes surrounding discovery and capture work rather than adding a typing-rate setting.

## Validation

- Full Python suite: **944 passed**, **439 subtests passed**, 24.20 seconds. JUnit: `.jarvis/qualifications/control-speed-2026-10-09.xml`.
- Control-center tests: **42 passed**; route generation and TypeScript checking passed.
- Isolated live Chrome qualification: **15/15 interface checks**, including focused-editor typing; typing, clicking, scrolling, frames, navigation, stale-target rejection, and human-verification stop passed.
- Live deterministic 11-step workflow: **637.12 ms**, zero model calls and zero resume replays. Expanded interface matrix: **1223.50 ms**. These are local fixture measurements excluding browser startup, not proof of faster model-planned missions or external-site loading.
- Whitespace/diff validation passed. No Rust source changed in this pass.

The discovery benchmark was read-only. Native delivery and existing-window activation used test doubles; no personal desktop application was launched or typed into for this pass. Real native app response times remain unmeasured here. Model-provider availability and application loading can still dominate tasks that need planning.

## Activation and examples

The Control Center worker revision is now **33**. With the updated dashboard loaded, its existing lifecycle replaces an idle older Python worker before the next mission. An active mission must finish or be cancelled first; it is not silently restarted. Existing Full Access and confirmation rules remain in effect.

Supported fast commands include:

```text
please open Notepad
launch Notepad then type "hello"
type "hello"
```

For the last command in a browser, the intended editor must already have focus. Missing or uncertain focus causes a pre-input rejection rather than typing into a guessed field.
