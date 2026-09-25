# Discord first-chat navigation — September 25, 2026

## Reported failure

Dashboard mission `7ad6fcd8-e3ae-4824-80ad-8e815a71449c` / checkpoint `task-1790304230640-cfd89b86` attempted `OPEN DISCORD AND PRESS THE FIRST CHAT`. It launched Discord, made 12 model calls during navigation/recovery, and ended with the provider's 429 token-quota response.

The existing exact-name navigator did not handle the current Direct Messages sidebar. Discord exposes conversation links with decorated Unicode names and role/pinned metadata, while the surrounding list also contains Friends, Nitro, Shop, and Quests. The model first changed the decorated name and later tried coordinates. Neither approach produced verified completion.

## Implementation

- Reuse the existing deterministic compiler, ordered execution, permission checks, checkpoints, and task graph. The reported command now compiles to `launch_installed_app` → `discord_select_chat` without a model call. Supported ordinal chat clauses also work in explicit app sequences. Unknown clauses remain intact for planning; they are never silently dropped.
- Resolve exactly one accessible Direct Messages list, then query only its visible conversation links. Accept only Discord `/channels/@me/<conversation-id>` routes, exclude unrelated navigation links, and order eligible controls by fresh visible geometry. Reject ambiguous scopes, duplicate routes, and missing ordinals.
- Preserve decorated names when separating Discord's explicit `(direct message)` / `(group message)` label metadata. Do not transliterate names or search for guessed text.
- Prefer semantic UIA Invoke/Select. Use a guarded Rust click only when activation patterns are absent. An uncertain semantic action never triggers a second mutation. Recheck process, foreground, permissions, control binding, and conversation route before native dispatch.
- Verify the exact application-document route **and** matching conversation composer. The same proof permits an already-open chat to complete without repeated input. A matching composer alone or changed route alone is insufficient.
- Optional compiled typing clauses remain unsent and preserve literal text spacing. The existing exact-write completion guard now also covers Discord chat typing. This turn's live test did not type or send any message.
- Worker revision 20 loads the updated code on the next idle-worker replacement. Active missions are not replaced or restarted.

## Live result

The exact reported command ran through the dashboard after the change.

Dashboard mission: `72fbfe2d-bf08-496d-8c50-30ab6c7490e5`.

Checkpoint: `task-1790304746976-43e4dc6d`.

| Step | Duration | Verification |
| --- | ---: | --- |
| Open Discord | 1,451.54 ms | Visible foreground Discord process/window |
| Open first chat | 2,572.28 ms | UIA Invoke; exact document route and matching composer |

Total recorded mission time: **4.157 seconds**. Two tools, two verifications, zero failures, zero model calls. The dashboard returned `COMPLETED`. The navigation action used **Windows UI Automation**, not Rust or coordinate clicking. The first conversation was not already open, so this exercised an actual navigation transition.

The operator started another mission afterward; further live input was stopped to avoid interference. No Discord draft was changed and no message was sent.

## Regression validation and limits

- **486 Python tests passed**, including a real-adapter fixture with the model deliberately unavailable, scoped ordinal ordering, Unicode names, server-to-DM navigation, already-open behavior, exact route/composer verification, permission revocation, focus changes, uncertain activation, and guarded Rust fallback.
- **44 workspace tests passed**.
- `git diff --check` passed.

The fallback to Rust is covered by regression fixtures; this live mission used semantic activation. The live result validates the current English Discord desktop interface and the first visible chat. It does not establish an all-application latency guarantee. Provider quotas remain unchanged for missions that genuinely need model reasoning.
