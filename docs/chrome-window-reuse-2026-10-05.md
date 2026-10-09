# Chrome window reuse — 2026-10-05

Google searches and URL navigation now resolve the user's open Chrome before
considering a separate automation browser. Foreground Chrome takes priority;
otherwise the resolver uses Windows window order. Chrome starts only when no
existing browser window is found. An unreadable or ambiguous existing surface
returns an actionable failure instead of launching another browser.

## Execution and verification

- A CDP endpoint is used only after binding its listener to the selected Chrome
  process and process creation time. A previously connected, different endpoint
  cannot steal a command.
- CDP selects the currently focused/visible page instead of the last-created
  page. Ambiguous selection clears the target and fails closed.
- Ordinary Chrome without CDP uses a narrow Windows UIA/Rust toolbar adapter for
  navigation, Google searches, first-result navigation, and explicit new tabs.
  Arbitrary DOM interaction still requires matching CDP access.
- Toolbar input validates window/process/control identity, focus, address
  selection, and text readback. Completion requires the independent page-document
  URL, not an address-bar draft. Human-verification challenges stop execution.
- Compound search/result actions retain their selected backend/window. Explicit
  CDP new tabs verify the original and new page's window IDs before navigation.
- Native-window search completion uses successful mission traces plus the same
  live window's tab inventory, avoiding false failures from unrelated CDP tabs.
- Worker revision 24 loads the changes on the next mission and preserves active
  mission protection during worker replacement.

## Runtime startup repair

Windows currently excludes ports 7435–7534, including the previous fixed daemon
port 7443. Startup now probes the preferred loopback port and uses an OS-selected
available port when necessary. Rust configuration, discovery, Python, and the
dashboard receive the same port; mutual TLS and explicit permissions remain in
force. The repaired runtime reported `rust_input_ready=true` and dashboard HTTP
200. Browser pairing must be refreshed after this runtime restart.

## Validation and limits

- Regression coverage includes existing-window priority, stale connections,
  exact-window compound operations, startup races, explicit tabs, lost focus,
  drafts, dialogs, challenge detection, uncertain delivery, and completion.
- All workspace TypeScript checks and 69 workspace tests passed.
- A real isolated headless Chrome passed Unicode/iframe typing, clicking,
  scrolling, navigation, same-window tab creation, and a nine-step workflow with
  zero model calls and no repeated steps on resume. The workflow took 566 ms in
  this local fixture; this is not a latency guarantee for external sites.
- Headless Chrome exposed ambiguous focus across tabs; the selector correctly
  rejected that inventory instead of guessing.
- The ordinary Chrome UIA/Rust route has mocked regression coverage and its
  accessibility structure was inspected read-only. End-to-end native navigation
  in the user's personal Chrome was not completed in this session.
- Automatic dashboard reconnection was blocked by the browser tool's URL policy.
  No alternative browser surface or authentication bypass was attempted.

## Mixed WhatsApp / Google mission repair — 2026-10-06

The reported mission's checkpoint confirmed that WhatsApp opened, chat 1 was
selected, and `h` was read back exactly as an unsent draft. The failure occurred
after Chrome received Ctrl+T: the new document temporarily exposed no URL.
Subsequent `task_verify` calls had no successful post-action observation.

- The exact compound command now compiles into four ordered deterministic steps:
  launch WhatsApp, select chat 1, type `h` without submitting, and search Google
  for `a cat` in an explicit new tab. Quoted text stays text, and unsupported
  trailing instructions cause the whole command to fall back to planning.
- An empty document URL is accepted only for a newly created tab with the same
  bound Chrome window, one additional tab, a changed document identity, and an
  empty focused address field. Completion still requires the requested URL to
  be independently readable from the document.
- Temporarily unavailable document observations are polled within the existing
  deadline; input is not repeated. Ambiguous windows and controls still fail
  closed.
- Uncertain action outcomes receive one fresh read-only screen observation per
  review. This does not declare success or replay the action. Failed observation
  remains a failure, and retry limits remain enforced.
- Worker revision 25 loads these changes on the next mission without replacing
  a worker that is still executing. Recovery observation bookkeeping is cleared
  when the worker resets for a new mission.

Regression tests cover the reported command, successful and failed browser
steps, preserved completed drafts, blank tabs, missing URL observations, lost
focus, ambiguous controls, and bounded verification recovery. The personal
WhatsApp/Chrome mission has not been replayed; the original draft was already
verified in its checkpoint.
