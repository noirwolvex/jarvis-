# Contextual control knowledge — 9 October 2026

JARVIS now has a packaged control procedure library integrated with its existing planner and tools. It supplies relevant instructions locally before a model decision, instead of requiring repeated skill discovery or putting every control procedure into every prompt. It does not introduce a new executor or replace the existing deterministic mission compiler.

## Coverage and use

The 17 guides cover application launch and window identity; desktop and browser text entry; exact, ordinal and relative targets; browser navigation and dropdowns; Chrome tabs; Google search; desktop and browser scrolling; visual pointer control and dragging; dialogs; workflow execution and recovery; Office COM; files and terminals; Discord; WhatsApp; and YouTube.

Each guide contains supported tools, preconditions, procedures, verification, recovery, pitfalls, and local source references. All required tools and referenced files are checked by tests. English and Arabic keywords select procedures; explicit desktop/browser context narrows selection while mixed missions and failed-tool recovery can retrieve across surfaces.

- `core/resources/control_guides.json` is the repository-owned catalog. Workspace skills, page text, and tool errors cannot insert trusted catalog entries.
- `core/control_knowledge.py` validates and caches the catalog once per Python worker. Automatic context adds up to three whole procedures within a 5,200-character budget. No model, network, screenshot, or UI call is needed for retrieval.
- `control_guide` is a read-only reference tool. An empty query lists available topics; task keywords or a guide ID retrieve details. `include_schemas=true` reads exact argument schemas from the registered tools, avoiding a second hand-maintained schema set.
- Guide and built-in skill retrieval follow the active mission's exposed tool profile. A browser-only task does not receive hidden terminal or Office procedures. Registered permissions remain authoritative.
- Existing `desktop-operator` and `browser-operator` skills now include applicable procedures. The new `control-mechanisms` skill provides the broader available reference.

Example reference query, with no desktop side effect:

```json
{"query":"browser_text","surface":"browser","include_schemas":true}
```

Known deterministic commands still run through the existing fast path. Model-driven work now receives relevant procedures automatically, including dedicated application operations and ordered workflows that avoid unnecessary model decisions between known actions. An unfamiliar detail can be looked up without rereading the entire library.

## Correctness and compatibility repairs

References cannot act as screen evidence. `control_guide`, `list_skills`, and `load_skill` cannot complete a task step, verify an action, clear an uncertain mutation, populate execution-backend evidence, or serve as a workflow action/checkpoint. Tests include misleading `VERIFIED:` text in reference content and confirm that real observations are still required.

The app-discovery integration also needed a repair. The core resolver's new `stop_when_exact` argument was incompatible with both previously installed discovery wrappers. The wrappers now preserve the argument and delegate exact resolution to the optimized core. Listing remains exhaustive. Exact resolution avoids a second cache that could restore failed launch entries or extend their lifetime. Listing caches also invalidate on relevant environment and working-directory changes.

Typing procedures use the existing semantic-capable `interaction_type` path for desktop editors, retaining guarded native fallback and exact readback. Browser focused-field typing uses the existing fresh focused-editor resolution. Procedures preserve drafts, distinguish delivery from verification, and never treat a typing request as permission to send.

## Validation and measured limits

| Check | Result |
| --- | --- |
| Full Python suite | 967 passed; 502 subtests passed; 31.85 seconds |
| Control Center tests | 42 passed |
| Route generation and TypeScript checking | Passed |
| Isolated live Chrome interface matrix | 15/15 checks passed: text, focus, controls, scrolling, dialogs, frames, loading, stale targets, and navigation |
| Live ordered workflow | 11 steps verified in 794.28 ms; zero model calls; zero replayed steps on resume |
| Live human-verification handling | Automation stopped at the fixture challenge |
| Whitespace validation | Passed |

Local reference retrieval measured 0.126–0.264 ms median across six representative English tasks, each sampled 100 times after warm-up. Selected context occupied 1,807–4,706 characters. This measures reference assembly only, not model latency or end-to-end task speed. No performance threshold is asserted from a single machine's timings.

The browser fixture used its own headless Chrome profile and loopback pages. Native app discovery/cache behavior was tested with controlled OS sources; personal desktop apps, messages and accounts were not touched. No external model was called for this validation. Better instructions and reduced lookup work do not prove error-free execution or faster model-planned missions on arbitrary third-party applications.

Commands used:

```powershell
rtk proxy python -X utf8 -m pytest -q
rtk proxy npm.cmd run test --workspace @jarvis/control-center
rtk proxy npm.cmd run typecheck --workspace @jarvis/control-center
rtk proxy python -X utf8 -m scripts.verify_browser_control
rtk proxy git -c core.safecrlf=false diff --check
```

## Activation and maintenance

Control Center worker revision is **34**. After the updated dashboard code is loaded, the existing lifecycle replaces an idle older Python worker before the next mission. An active mission must finish or be cancelled first; the upgrade does not silently restart it. No Rust rebuild or new permission grant is required for this change.

Add or revise procedures in the catalog using real registered tools and current source references, then run `tests/test_control_knowledge.py` and the relevant execution tests. Keep procedures bounded and do not place credentials, private task data, fixed screen coordinates, or authorization overrides in the library. Catalog edits take effect in a new worker because the validated catalog is cached for that worker's lifetime.
