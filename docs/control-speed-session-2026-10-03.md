# Control speed and browser reconnection — October 3, 2026

## Execution and perception

Windows ordinal, focused and selected target resolution now queries UIA for the required control type. Simple selection reads only the necessary parent relationship; parent/adjacent selectors retain their full context. Fresh target identity, foreground validation, cross-container ambiguity checks and post-action verification are preserved.

The regression fixture with 600 unrelated text nodes and two buttons now reads metadata for 3 nodes instead of 603: 99.5% fewer metadata reads. A second fixture resolves buttons beyond 900 unrelated nodes without hitting the full-tree limit. This measures avoided work, not a claim that every task is 99.5% faster.

Browser semantic observation combines the human-verification check and DOM snapshot in one CDP evaluation instead of two. Candidate bounds are read once and reused; offscreen controls avoid style reads. Recognition includes custom tabindex/onclick widgets, disclosure summaries, image/SVG icon labels, search inputs, numeric inputs, multiselect lists and labels inside open shadow roots. Focused editors receive priority in bounded snapshots. Inferred controls keep fresh version-bound node targets when a role locator would be invalid.

Existing semantic click, exact Unicode typing/readback, focus, scrolling and navigation paths are reused. Canvas controls, closed shadow roots, incomplete accessibility providers and large/truncated scenes still require other observation methods; these changes do not promise recognition of every visible object or error-free execution on every application.

## Authenticated runtime session repair

The launcher previously paired only the default browser. Other profiles, including an embedded browser, had no signed cookie. Runtime restart also invalidated old cookies, while the dashboard could still appear connected.

- A read-only `/api/session` status distinguishes a connected browser from one that needs pairing; it never creates credentials.
- The dashboard provides an explicit reconnect screen and verifies that the browser retained its cookie before showing command controls.
- Selecting `.jarvis/control-session.json` posts the current token to the local pairing endpoint. The POST requires a local same-origin request, a pairing header and a bounded strict JSON object.
- Cookies remain signed, HttpOnly and SameSite=Strict. Runtime secrets still rotate at startup. Pairing grants neither Full Access nor terminal permission.
- Windows file inheritance is removed and the current user alone receives access before the token is written. Atomic replacement prevents a failed write from corrupting the prior pairing file. Tokens are not logged or placed in browser storage.
- `npm run dev -- --no-open-browser` prepares the file without opening another browser window.

After startup, reload the existing dashboard and use **Choose pairing file** to select `C:\Windows\System32\jarvis-\.jarvis\control-session.json`. Select the new file again after a runtime restart.

## Validation

- Full Python suite: 705 tests and 256 subtests passed after the control/perception and pairing-file changes.
- After adding the no-open-browser option, all 26 runtime-bootstrap tests and 6 subtests passed.
- Workspace tests: 42 Control Center, 21 runtime and 6 database tests passed. TypeScript checks passed.
- Auth regressions cover missing, forged and rotated cookies; invalid/stale tokens; missing/hostile origins; cookie flags; oversized/invalid JSON; and no credential leakage.
- Runtime file regressions cover permission-before-write ordering, rotation, failed permission/replace cleanup and preserving the previous file. Live Windows ACL inspection confirmed only the current user on the generated file.
- The managed runtime started with Rust input ready and dashboard HTTP 200, using the no-open-browser option.

The browser tool's security policy prevented inspecting or pairing the user's actual dashboard tab. Route and code tests passed, but the final user-browser reconnection and visual appearance remain unverified. No messages, purchases or destructive desktop tasks were executed for this update.
