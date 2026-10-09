# Desktop and browser control repair — 30 September 2026

## Changes

- Preserved desktop window identity and browser iframe identity in resolved action targets. Browser scene deltas now include target-version changes; long missions retain at most 128 scene snapshots.
- Rejected mixed browser/desktop targets before input. Fixed the advertised desktop shortcut selector argument and focused explicit shortcut targets before dispatch.
- Connected `semantic_scroll` to the CDP owner-thread dispatcher. Previously the public scroll tool reached an unsupported operation. Corrected the browser shutdown reply protocol as well.
- Kept native pre-dispatch errors distinct from uncertain delivery. Missing targets, stale bindings, unavailable native preparation and failed final guards no longer create a fictitious completed input requiring verification. A delivered write or uncertain transport still requires observation and cannot be replayed automatically.
- Required both `executed=true` and `simulation=false` before reporting Rust input delivery.
- Improved Discord DM row/route resolution, rejected conflicting destinations, and avoided unnecessary sidebar scans during route verification. These Discord changes have regression coverage; no live Discord messages were sent.
- Added Windows scan codes to Rust virtual-key events while retaining Unicode events and extended navigation-key flags. Updated the Python worker revision to 21.

## Live verification

`npm run verify:rust-live` passed against the optimized Rust daemon through the actual Python JSON-lines worker. The isolated temporary window independently verified:

- real screen frame and BMP preview on display 65537 (1920 × 1080);
- mouse click focusing the intended text field;
- exact English/Arabic text input;
- Ctrl+A delivery and replacement of the selected text;
- Home moving the caret, followed by exact prefix insertion;
- mouse click invoking the test button callback.

Measured worker round trips: capture 49.93 ms; clicks 44.91/49.62 ms; initial typing 46.00 ms; subsequent typing 2.41–3.83 ms; Ctrl+A 3.13 ms; Home 2.64 ms. These are local fixture measurements, not latency guarantees for other applications or missions.

The qualification harness now pumps its GUI while awaiting worker responses and binds every probe to its own HWND/process. It waits for that window to become foreground rather than typing into whichever application is active. Its Select All handler uses delivered Windows VK/Control state because Tk's default keysym binding did not recognize Ctrl+A under the current keyboard environment. Thus the test proves shortcut delivery and the fixture's reaction, not every application's shortcut implementation.

`npm run verify:browser-live` passed against an isolated headless Chrome profile and loopback fixture pages through JARVIS's real CDP thread, tool registry, semantic resolver and interaction tools. It verified Unicode fill, append, iframe fill, button result, scrolling, navigation URL, and preservation of one browser tab. Last action round trips in that run were 3.71 ms resolution, 66.04 ms iframe typing, 18.92 ms hotkey dispatch, 74.20 ms navigation click, 25.60 ms postcondition wait and 4.38 ms scroll. Hotkey dispatch alone is not reported as a verified browser shortcut outcome.

No personal browser profile or chat content was used by either fixture. No message was sent and no existing draft was changed.

## Regression checks

- Python: 585 tests passed.
- Rust native-feature suite: 47 passed; 4 opt-in benchmark/subprocess fixtures ignored.
- Workspace tests: 46 passed, including Control Center, runtime and database checks.
- Workspace type checks passed.
- Rust formatting and Clippy with all targets/features and warnings denied passed.

Full Access, emergency stop, fresh target checks, foreground binding, human-verification boundaries and independent outcome checks remain enforced. Arbitrary applications and long missions can still fail; the fixture results do not establish zero-error operation.

## Control Center hydration repair

The pasted hydration report showed browser-added `bis_skin_checked="1"`, body
`bis_register`, and UUID-shaped `__processed_*__="true"` attributes. These were
absent from the server response and application markup. Client instrumentation
now removes these specific markers synchronously before React hydration. The
processed marker is removed only when the accompanying `bis_*` evidence exists.
The cleanup runs once; genuine hydration diagnostics remain enabled.

`python -m scripts.verify_dashboard_hydration` tests the running development
dashboard at `http://127.0.0.1:3000/` in isolated headless Chromium contexts. It
holds application script requests, annotates the parsed DOM as the extension
would, then resumes the actual Next bootstrap. It does not run desktop missions.

Before the fix, the injected extension case reproduced the reported warning.
After the fix, all five browser scenarios passed:

- Clean page and extension-marked page: no hydration warnings, interactive theme
  controls, and no remaining known extension markers.
- Unrelated attribute mismatch and a mixed extension/unrelated mismatch: the
  unrelated attribute remained and React still reported the genuine mismatch.
- A processed UUID marker without `bis_*` evidence: left intact and reported by
  React, confirming that the cleanup does not strip arbitrary processed markers.

All 19 Control Center tests and the workspace's TypeScript check passed after
this repair. The running development server loaded the new instrumentation.

## Visible-control execution improvements

The next review improved the existing semantic paths without replacing the Rust
input engine or loosening its foreground, permission, or stale-target checks:

- UIA observation includes data rows, sliders and numeric spinners. Browser scenes
  include search boxes, list boxes, numeric inputs, tree items and specialized menu
  items. Browser input roles now agree with the DOM role locators used to act on
  them; native input-button labels come from their visible value.
- Bounded browser snapshots prioritize the focused editor. Ordinal resolution
  rejects truncated scenes, where an omitted control could change which item is
  actually first or second.
- Desktop resolution preserves unique automation IDs even when labels repeat.
  Browser resolution preserves node/version identity when selection or focus
  disambiguates otherwise identical controls. Common DOM/UIA role aliases retain
  the backend's actual control type.
- Legacy browser clicks and writes pin their resolved element across preflight.
  Replacement, hidden or disabled controls cannot silently receive the input.
  Writes read back the exact value on the same connected element. No uncertain
  mutation is automatically replayed.
- Browser result checkpoints support exact input/textarea/contenteditable values
  and focus. Desktop checkpoints support exact text, enabled and hidden states;
  absence requires a complete fresh query. Disabled but visible named controls
  remain observable without becoming eligible for input.
- Native append handles UIA providers that count a final CRLF as either one or two
  character units. It verifies the exact preserved prefix with at most two
  read-only range moves before guarded delivery.
- Worker revision 22 loads these changes on the next mission after an older
  worker becomes idle. Active missions are not terminated by worker replacement.

Validation after these changes: 616 Python tests passed; 46 workspace tests and
all workspace TypeScript checks passed. The live isolated Chrome qualification
verified typing, iframe typing, clicks, scroll, navigation and one-tab reuse.
Its new nine-step workflow ran through the actual dispatcher and durable journal
in 542.49 ms, with zero model calls; resuming it caused zero action replays.
This is a local fixture measurement, not an external-application latency promise.

The latest Rust qualification connected the Python worker to the native daemon
but could not obtain foreground focus for its temporary test window. It stopped
without dispatching test input. The earlier successful native qualification above
remains historical evidence; this rerun does not establish a new live input result.
