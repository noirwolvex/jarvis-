# Typing failure: ambiguous editor followed by rejected clicks

The [September 24 follow-up](device-control-2026-09-24.md) fixes a remaining search/composer label collision found in a newer trace, reduces UIA work, and supersedes the validation counts below.

Reported task: `591c6691-1d2a-4061-8fa4-85bd5373e79b`.
Saved checkpoint: `task-1790119433991-80085ff0`.

## What failed

The checkpoint records the mission `OPEN WHATSAPP AND PRESS THE FIRST CHAT THEN WRITE HI`. Application launch and native chat selection both returned verified evidence. Typing then failed with:

```text
ERROR executing ui_type_native: RuntimeError: Target '' has 8 exact visible enabled matches
```

The subsequent UI inspection contains repeated wrappers for the same WebView document runtime identity. The resolver counted wrappers as separate candidates, and treated document roots as possible editors. Recovery attempted a coordinate click before reviewing the failed action, then repeated a click twice without the required `screen_observe`. Those clicks were rejected before dispatch. The final three-attempt message hid the original editor-resolution failure.

The trace establishes ambiguous target resolution, duplicate UIA entries and missing recovery observation. It does not establish a Rust keyboard injection failure: the typing request failed before native delivery.

## Fix

- Deduplicate descendants using native runtime identity and process ID before counting candidates or applying the 700-control limit. Separate identities remain separate; controls without native identity are not merged by label or geometry.
- Exclude Chromium `RootWebArea` document roots and known read-only fields from editor resolution, including selector-based typing. Missing ValuePattern remains supported for editors that use native keyboard input.
- Keep the WhatsApp typing step explicitly bound to the WhatsApp window.
- After a missing, stale or unstable screen precondition rejects a coordinate action, take a read-only observation and replan. Do not replay the rejected click or continue dependent typing automatically.
- Preserve the distinction between rejected input and delivered mutations. Add specific semantic-target recovery guidance and include the underlying error in repeated-failure reports.
- Increase the worker revision to 11. Retire an outdated idle Python worker without sending the emergency-stop protocol, which would otherwise latch the shared Rust daemon. On Windows, retire its owned launcher process tree so an old child worker cannot survive. Active missions cannot be replaced by this update path; explicit emergency stops retain the stop protocol.
- Report a latched Rust emergency stop immediately as `WAITING_USER`, with a restart instruction. Do not enter model recovery or attempt later input in the batch when the native engine cannot execute.

## Validation

- **414 Python tests passed**, including twelve editor-resolution tests and six recovery tests.
- **17 Control Center tests passed**, including idle-worker replacement without Rust stop, cancellation during replacement, and refusal to replace an active mission.
- Next.js production build and TypeScript compilation passed.
- Git diff whitespace check passed.

The regression fixture reproduces eight wrappers representing a document root, search field and composer. It verifies unique composer resolution, exact Unicode readback, preservation of genuinely ambiguous controls and selector behavior. An integration fixture runs the exact reported mission through the deterministic executor and actual semantic typing functions with native delivery mocked. It completes with one typing dispatch, no coordinate recovery and no model call.

The live dashboard was reachable in hybrid mode. Its development bundle includes the worker refresh; the next mission uses the worker-replacement lifecycle. The saved failed task remains historical evidence. No user's chat was typed into or sent during this repair, and no running daemon was stopped. WhatsApp was absent from the computer-use app/window listing, so live application qualification was not performed.

A retry made during repair (`task-1790120184073-31dbdced`) revealed that the old updater had already left Rust's emergency stop latched. Its fallback loop subsequently hit a provider 429 quota error. The fix prevents future update-induced stops and avoids model recovery when Rust is latched; it does not silently clear an existing emergency stop. **Restart JARVIS once before retrying the typing mission.** If a provider quota error remains after that, it is a separate model-service limit.
