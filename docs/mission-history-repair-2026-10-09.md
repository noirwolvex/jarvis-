# Mission-history failure repair — 9 October 2026

The nine reported dashboard runs matched the latest saved task traces. Seven failed with `Invalid or duplicate control guide id`; the two app-only File Explorer requests completed. Five failed requests reached no execution tool at all. Two first encountered a Chrome error, then crashed while constructing the recovery prompt.

## Trace findings

| Reported request | Actual evidence |
| --- | --- |
| Open File Explorer | Both runs verified the launched window, in approximately 0.89 and 1.14 seconds. |
| Open File Explorer and press Downloads / go to Downloads | Prompt construction failed before any tool executed. These traces do not show a failed Downloads click. |
| Open File Explorer and | Incomplete request reached the same prompt error. It must not be interpreted as a successfully completed compound command. |
| Open new tab and search for a cat | Fell through to planning, then encountered the prompt error. |
| Search for a cat in Google | Passed `A CAT IN GOOGLE` as the query. Existing Chrome then exposed a temporarily missing/ambiguous page or address field; recovery hit the prompt error. |
| WhatsApp → first chat → write H → Discord → Google | Launch, chat selection, unsent `H` readback, and Discord launch all verified. Google search failed before Chrome published its active document; recovery hit the prompt error. |

Corresponding checkpoints: `task-1791512105605-f746dcfb`, `task-1791512102084-a874cbcd`, `task-1791512095503-b0327e4b`, `task-1791512093799-d5c57e4c`, `task-1791512043967-eac65c17`, `task-1791512022453-e0e353b5`, `task-1791511930237-9deb1dae`, `task-1791511907315-fb9fc4aa`, and `task-1791511833256-92315c9b`.

## Repairs

The catalog validator on disk already accepted underscore IDs and all 17 packaged guides. The dashboard-owned Python worker had started before that correction and kept the earlier imported validator. Passing tests in a new Python process did not demonstrate that the resident worker had loaded the same source.

The dashboard now fingerprints Python execution source and packaged JSON resources before mission dispatch. It preserves an unchanged worker, replaces an idle outdated worker through the existing lifecycle, and rejects a second mission rather than stopping an active one. The fingerprint excludes runtime traces, credentials, and bytecode. Worker revision is **35**.

Optional reference loading is now isolated from execution. A missing, malformed, oversized, or invalid catalog returns a fixed unavailable notice; the planner retains core rules and registered tool schemas. The mission records a status notice once. No invalid guide text enters the prompt and no reference result counts as observation, execution, or verification.

Google command parsing separates a terminal engine phrase from the query. `SEARCH FOR A CAT IN GOOGLE` compiles to `google_search(query="A CAT", new_tab=false)`. `OPEN NEW TAB AND SEARCH FOR A CAT` compiles to the same operation with `new_tab=true`. Quoted search terms remain literal; incomplete or unsupported additional work remains with the planner rather than being silently dropped.

The existing-window Chrome adapter now uses bounded, cancellable, read-only polling when the accessible document or toolbar has not become ready. It focuses only the previously identified window when background publication requires activation. It retains exact window, document and editor bindings before input. It never replays a hotkey, text entry, or navigation after uncertain delivery. Persistent ambiguity, focus changes, dialogs and human-verification challenges still stop execution.

## Validation scope

| Check | Result |
| --- | --- |
| Full Python suite | 981 passed; 544 subtests passed; 27.71 seconds |
| Dashboard suite | 48 passed, including six new worker-source lifecycle tests |
| Route generation and TypeScript checking | Passed |
| Chrome adapter and adjacent browser regressions | 135 passed; 59 subtests passed |
| Isolated live Chrome interface matrix | 15/15 checks passed |
| Isolated ordered workflow | 11 verified steps in 660.08 ms, zero model calls and zero resume replays |

The exact reported Google commands and five-step WhatsApp/Discord/search sequence have compiler regressions. Catalog tests inject missing files, corrupt JSON, invalid IDs, duplicate IDs and oversized content. They confirm that the core prompt and verification requirements survive, with one status notice per mission. Worker lifecycle tests mock all process operations and verify that changing Python or resource content reloads only an idle worker, even without a manual revision bump.

The browser qualification uses a temporary headless Chrome profile and owned loopback pages. It verified 15 interface checks plus an 11-step workflow with no model calls or replayed steps. Existing-window Chrome publication and focus races are exercised with controlled UIA/native-input fixtures; this is separate from live CDP qualification.

The personal WhatsApp/Discord mission was not replayed. Its unsent draft was already verified, and rerunning the whole mission could append duplicate text. Downloads navigation still uses the existing semantic planner; this repair removes its prompt-construction crash but does not constitute a live verification of that folder on the user's desktop.

Load the updated dashboard before starting a new mission. Its next dispatch replaces an idle stale Python worker. Previously failed history rows remain accurate historical records.
