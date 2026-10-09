# Autonomous challenge result — 2026-10-07

**Result: incomplete.** This was a live run of the configured Gemini planner and
JARVIS browser tools in an isolated headless Chrome, not mocked model responses
or a prewritten execution sequence. It does not qualify physical mouse movement,
Rust keyboard injection, visual reasoning, personal applications or dashboard integration.

## Requested mission

Find the highest approved Orion revision among four records; ignore a newer draft
and a different project. Open it, dismiss a notice, wait for a delayed editor,
select its placeholder with Ctrl+A and type `JARVIS validation — مرحبا`. Type
`reviewed-r4` into an iframe editor. Scroll, save one local draft, verify its
receipt and summary, then write and independently verify two matching report files.

## Observed result

- Read the table and correctly identified Orion revision 4.
- Navigated to `/document/orion-r4` and dismissed the notice.
- Kept one tab.
- Did **not** type either value, scroll, save a draft or produce the report files.
- Paused at a real provider HTTP 429 quota response, preserving the checkpoint.

The local fixture recorded the document visit and notice dismissal, zero saves,
and zero scroll events. The final screenshot independently shows the original
placeholder and an empty iframe input. Completion was not claimed by the runner.

## Timing and failures

Task: `task-1791328228427-051cc8f4`.

| Measurement | Result |
| --- | --- |
| Total test time, including setup/cleanup | 28.42 seconds |
| Model calls | 19 |
| Time waiting for model responses | 24.804 seconds |
| Total recorded tool time | 0.761 seconds |
| Successful document click | 101.06 ms |
| Successful notice dismissal | 102.25 ms |
| Tool failures | 3 |

The planner first supplied a snapshot node without its required version. That
pre-dispatch error was treated as an uncertain action and triggered additional
observation/verification turns. Another attempt mixed selector and role target
forms, which the schema rejected. Recovery eventually used a valid target.
Many remaining calls were separate planning/bookkeeping operations. The observed
bottleneck was model/recovery orchestration, not the two successful browser clicks.

## Reproduction and artifacts

Run `python -m scripts.verify_autonomous_mission` to perform an opt-in live test.
This consumes configured provider quota. The runner restricts actions to its owned
browser and new workspace, applies a 240-second cancellation limit and retains
independent state, progress events, screenshots and result JSON under
`.jarvis/qualifications/`.

Evidence for this run: `.jarvis/qualifications/autonomous-20261007-021026/`.
Checkpoint: `.jarvis/traces/task-1791328228427-051cc8f4.json`.

Two earlier attempts were invalidated by qualification setup defects: an overly
narrow approval responder, then removal of an internal browser backend required
by universal interaction tools. Those defects were corrected before the measured
run above. A later isolation check also found that lazy dotenv loading placed the
measured run's checkpoint in the project trace directory rather than its test
workspace. No report-writing step was reached. The runner now loads configuration
before setting its workspace and asserts isolation before running any mission;
that assertion was separately qualified without another paid model run.

This challenge therefore does not establish reliable completion of difficult
missions. It provides a concrete failed trace for improving target validation,
verification bookkeeping and the number of model round-trips.
