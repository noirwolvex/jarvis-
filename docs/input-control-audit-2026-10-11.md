# Mouse and keyboard audit — 2026-10-11

This pass covers Python input delivery, the Python/Rust transport, Rust pointer trajectories, and Windows native key encoding. It preserves the existing semantic targeting, permission boundaries, emergency stop, and verification requirements.

## Fixes

| Finding | Change | Regression evidence |
| --- | --- | --- |
| Clipboard fallback waited before pasting without rechecking focus or cancellation. | Read back the clipboard, recheck the foreground window and cancellation immediately before the shortcut, and remove fixed sleeps. | Focus loss, cancellation, missing foreground, and clipboard replacement prevent input. |
| A failed paste shortcut could leave Ctrl held or report an ordinary error after partial delivery. | Attempt key releases, restore PyAutoGUI settings, and report uncertain delivery without replaying the paste. | Shortcut failure sends cleanup releases once and preserves the library settings. |
| Failure to resolve a named editor could fall back to typing into the current window. | Reject before delivery when the named editor cannot be resolved. Retain adaptive exact readback for a successfully resolved UIA editor. | Missing-editor tests prohibit both native typing and clipboard fallback; delayed readback performs only one write. |
| Local request encoding/size errors consumed a Rust session sequence number without sending a request. | Allocate the sequence for the envelope but commit it only at dispatch. | Invalid Unicode, oversized requests, and unserializable arguments leave the next valid request in sequence. |
| Smooth movement could overwrite an external cursor move between samples. | Check the current position against the previous delivered point before every subsequent sample, in both engines. | Simulated external movement stops the trajectory without moving the cursor back. Rust drag uses the same guard and retains button-release cleanup. |
| Rust trajectories waited after rounding had already reached the destination; unchanged points could cause redundant delivery. | Finish at the exact destination and avoid duplicate movement events. Apply immediate endpoint completion to the Python path too. | Virtual-clock trajectory tests verify exact endpoints, cancellation, late scheduling, negative coordinates, and failed delivery. |
| Native key mapping lacked common navigation/modifier aliases and omitted Windows-key extended flags. | Add Insert, PgUp/PgDn, left/right modifier and Windows-key aliases, and application-menu keys with the appropriate encoding. | Windows key-encoding tests check virtual keys, extended flags, nonzero scan codes, and rejection of unknown names. |

The Windows encoding was checked against [Microsoft's keyboard input reference](https://learn.microsoft.com/en-us/windows/win32/inputdev/about-keyboard-input).

## Performance evidence

These are bounded path improvements, not an end-to-end mission speed claim:

- Clipboard fallback removes **100–550 ms of unconditional sleeps per paste**. Clipboard and application response time still apply; input delivery is not reported as verification.
- A one-pixel Rust movement configured for 80 ms finishes at **40 ms** in the virtual-clock test, when rounding first reaches its exact endpoint. It emits one movement event instead of an additional duplicate endpoint event.
- A one-pixel Python movement configured for 100 ms finishes at **64 ms**, without the old additional 16 ms sleep after arriving.
- An unchanged Rust cursor target validates authority without moving or waiting.

## Validation

- Full Python suite: **1,087 passed + 607 subtests**, 29.11 seconds.
- Rust release suite with `native`: **52 passed**, 4 intentionally ignored (one opt-in read-only benchmark and three subprocess fixtures).
- Rust Clippy: all targets/features with warnings denied — passed.
- Optimized native `jarvis-daemon` build — passed.
- Diff whitespace check — passed.

The Rust interoperability test uses `JARVIS_TEST_PYTHON` set to the installed Python 3.14 executable. The default `python` command on this device resolves to the Windows Store alias; no system Python settings were changed.

Tests use virtual cursor state, mocked input delivery, key encoding checks, and isolated transport/process fixtures. They do not inject input into the user's live applications. Real WhatsApp/Discord typing and overall mission latency therefore remain unqualified by this pass. No running `jarvis-daemon` process was present at the final build check; the normal JARVIS startup will load the rebuilt native engine.
