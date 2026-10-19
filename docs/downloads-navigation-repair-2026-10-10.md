# Downloads navigation repair and control libraries — 10 October 2026

## Reported failures

The three reported `OPEN FILE EXPLORE AND GO TO THE DOWNLOADS` runs reached provider quota pauses. Two paused before any tool ran. The dashboard rejected those valid zero-tool checkpoints and displayed `failed` instead of `waiting user`. The remaining trace launched Explorer, then encountered ambiguous window targeting and exact-name mismatches: the real navigation control is a `TreeItem` named `Downloads (pinned)`, not a `ListItem` named `Downloads`.

Checkpoints examined: `task-1791552083967-455e2ea5`, `task-1791552081282-e0ade12d`, and `task-1791552027810-dc96d2a5`. A newer direct-folder trace, `task-1791559042432-9b276e82`, exposed a separate `int(None)` crash while activating Explorer.

## Changes

- Window activation now treats a transient null `GetForegroundWindow()` return as unverified state. It can recover within its bounded wait, without crashing or assuming focus succeeded. The existing-app launch check also handles null foreground correctly.
- The existing deterministic Downloads compiler remains the preferred route for the exact reported command. It calls the Windows known-folder adapter without a model request or a brittle sidebar-label match.
- Explorer location URLs are decoded once, preserving literal percent sequences in redirected folder names. Closed COM window objects are skipped, and COM initialization is balanced on errors.
- The folder verifier rechecks foreground after reading the actual Shell location, so a focus change during COM inspection cannot produce verified success.
- A failed activation attempt is followed by read-only polling, rather than repeated activation of the same HWND. Folder launch is still dispatched at most once. Cancellation, ambiguity and timeout do not produce completion.
- Backend telemetry records the actual Windows API and Shell COM boundaries. Restricted mode now explicitly denies known-folder navigation, even when a caller supplies approval.
- The dashboard accepts zero-tool `waiting_user` checkpoints with no fabricated execution evidence. It still rejects zero-tool completion, verified/completed graph nodes, or claimed execution engines.

Existing history rows are not rewritten. The existing source-fingerprint mechanism replaces an idle outdated Python worker on the next dashboard dispatch; active missions are not interrupted.

## Requested control libraries

All requested mechanisms were already declared and integrated before this repair:

| Component | Local verification | Existing integration |
| --- | --- | --- |
| pywinauto | 0.6.9 installed; import and UIA backend initialization passed | `core/semantic_ui_tools.py`, `core/desktop_input.py`, and native target resolution |
| PyAutoGUI | 0.9.54 installed; import passed | `core/desktop_control_tools.py` and the Python input fallback |
| Windows UI Automation | Windows COM API used through pywinauto's `uia` backend | Semantic window/control discovery, focus and value readback |
| Supporting Windows dependencies | comtypes 1.4.16; pywin32 312 | UIA COM and Shell integration |

The configured dashboard worker interpreter is `python` on PATH. The successful library checks used its Python 3.14 interpreter. Tests used that interpreter's absolute path because one invocation through the Windows `python` execution alias failed to resolve. No libraries were redundantly installed or upgraded. `requirements.txt` and README now explain the existing roles; UI Automation requires no separate pip package. Rust remains the preferred low-level input engine in Full Access.

## Validation

| Check | Result |
| --- | --- |
| Full Python suite | 1,011 passed; 560 subtests passed, 34.46 seconds |
| Focused Downloads, app activation and deterministic parser checks | 93 passed; 101 subtests passed |
| Dashboard tests | 50 passed |
| Dashboard route generation and TypeScript checking | Passed |
| Diff whitespace validation | Passed |
| Live Explorer navigation | Downloads sidebar activation produced the Downloads tab, breadcrumb and title |
| Live JARVIS read-only folder verification | Windows resolved the known folder; Shell COM matched exactly one selected Explorer HWND; foreground matched that HWND |

The live navigation used the computer-use API; the subsequent exact-folder and foreground reads used JARVIS's verifier. This is not a complete dashboard mission execution or a live test of JARVIS's launch/focus dispatch. Those dispatch branches, provider checkpoints, cancellation, focus races and retry limits are covered by automated fixtures. Provider quota availability remains external to these repairs. No personal files were opened or changed, and no messages were sent.
