# Interface recognition and control audit — 8 October 2026

## Result

The existing perception → semantic resolution → execution → verification path was audited and repaired without replacing the Python agent, Rust input engine, or browser adapter. Real isolated Chrome checks passed for discovery, typing, clicking, scrolling, dialogs, loading changes, frames, shadow roots, and navigation. Windows UI Automation and Rust behavior were checked through their automated suites; this run did not inject native input into a user's desktop application.

This is evidence for the surfaces below, not a guarantee of recognizing every control in every application.

## Repairs

- Browser names now respect `aria-labelledby` before `aria-label`, visible button text before tooltips, and inline image alternative text. Hidden/decorative/editor descendants no longer contaminate button names. Missing label references correctly fall back to an available label.
- Open shadow-root controls retain composed ancestry. Hidden/inert ancestors and disabled fieldsets are respected. Unsupported implicit roles are no longer advertised for password/date/file/color inputs.
- Labels shortened to the snapshot's 180-character limit retain a version-bound node identity. They no longer become an exact role/name locator containing an incomplete name. A real browser regression clicks the intended long-label button exactly once.
- Native progress indicators and heading levels 4–6 are recognized; progress indicators reach the perception layer's loading cues.
- Visible iframes and canvases explicitly report incomplete semantic coverage, including when the selected-node budget omits them. Frame metadata survives scene normalization. Callers can inspect a frame separately or request visual observation for visual-only regions.
- Browser ordinals reject missing, zero-area, or indistinguishable geometry instead of inventing a visible order.
- A Windows UIA scan that stops at 700 controls now reports truncation even if query filtering finds no match. The flag survives snapshot caching. Complete-tree operations still reject incomplete scans.
- Unknown window handles cannot establish alignment between semantic metadata and a screenshot.
- A human-verification stop propagates through `computer_observe`; it is no longer treated as generic missing metadata eligible for visual fallback.
- Dashboard worker revision is 32. The existing lifecycle replaces an idle older worker before the next mission. An active older mission must finish or be cancelled; it is not silently terminated by this revision change.

## Live qualification

`scripts/verify_browser_control.py` starts an isolated headless Chrome profile and an authored loopback fixture. It exercises production discovery and interaction tools. It does not attach to the dashboard, personal Chrome profile, Discord, or WhatsApp. The fixture's event log independently verifies input effects; text actions also require exact value readback.

| Surface / behavior | Verified outcome |
| --- | --- |
| Accessible labels, buttons, labeled icons | Correct names discovered; intended controls activated |
| Unicode and multiline typing | Exact readback after typing, Ctrl+A replacement, and append |
| Rich-text and open shadow-root editors | Exact readback in both editors |
| Duplicate buttons | Unqualified target rejected; second visible button activated without clicking the first |
| Checkbox and select | Checked state and selected value independently verified |
| Hidden and disabled controls | Rejected before input; no matching fixture event |
| Dialog | Recognized, field typed and read back, dialog closed |
| Delayed loading state | Waited for the renamed/enabled target, then activated it |
| Removed/replaced control | Old version-bound target rejected before input |
| Iframe | Discovered, explicitly inspected, typed, and read back |
| Canvas / bounded snapshots | Coverage gap or truncation reported rather than claiming complete understanding |
| Scroll and page navigation | Changed scroll position, resulting page content, and URL verified |
| Tabs | One tab retained during the workflow; explicit new-tab operation stayed in the same Chrome window |
| Ambiguous headless tab focus | Rejected and unsafe selected-tab state cleared |
| Human-verification marker | Observation and click stopped; no challenge-button activation |
| Ordered workflow and resume | All 11 steps completed; completed workflow resume replayed zero actions |

Latest measured run:

- Ordered 11-step workflow: **620.89 ms**, zero model calls, one fixture confirmation, zero resume replays.
- Expanded interface matrix: **14/14 checks**, **1169.74 ms**, 19 fixture events, zero model calls.
- Last recorded basic operations: resolve 5.35 ms; typing 60.38 ms; shortcut 19.50 ms; click 66.65 ms; state wait 40.74 ms; scroll 2.91 ms.

These are individual local-fixture measurements, not percentiles or a latency guarantee for external websites, native applications, or model-planned missions. Timings exclude browser startup. They demonstrate the deterministic execution path avoids model round-trips.

## Regression validation

| Check | Result |
| --- | --- |
| Full Python suite | **905 passed**, 391 subtests passed, 26.11 s |
| Rust native-feature suite | **47 passed**, 4 ignored opt-in/fixture entry tests |
| Control-center tests | **42 passed** |
| Control-center type generation and TypeScript checking | Passed |
| Expanded live browser qualification | Passed |

Python JUnit evidence is saved locally at `.jarvis/qualifications/interface-audit-tests.xml`. The Rust tests validate engine behavior and boundaries; they do not demonstrate fresh physical mouse/keyboard delivery to a desktop application.

Reproduce from the repository root:

```powershell
rtk proxy python -X utf8 -m pytest tests -q --junitxml=.jarvis/qualifications/interface-audit-tests.xml
rtk proxy python -X utf8 -m scripts.verify_browser_control
rtk proxy cargo test --manifest-path daemon/rust/Cargo.toml --features native -- --test-threads=1
rtk proxy npm.cmd run test --workspace @jarvis/control-center
rtk proxy npm.cmd run typecheck --workspace @jarvis/control-center
```

## Remaining qualification limits

- Canvas-drawn controls, closed shadow roots, unlabeled icons, virtualized lists, offscreen items, and incomplete accessibility providers can still require vision, scrolling, or a fresh observation. A coverage warning or screenshot is not proof that these controls were understood correctly.
- OCR/model vision accuracy and physical mouse/keyboard delivery into current versions of Windows applications were not live-qualified in this run. The desktop tests use controlled UIA/native test doubles; previous live native evidence is recorded separately in the earlier control reports.
- The browser fixture uses deterministic steps. The earlier larger real-model mission still did not complete because the configured provider returned HTTP 503. See [provider recovery retest](provider-recovery-retest-2026-10-08.md). This audit does not convert that failed mission into a success or remove provider latency.
- Foreground changes, stale state, ambiguous controls, permission boundaries, and emergency-stop checks remain enforced. No success is inferred solely from input dispatch.
