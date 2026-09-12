import { createRuntime } from './runtime.ts';
const runtime = createRuntime({ stepDelayMs: 10 });
runtime.events.subscribe(event => console.log(`${String(event.sequence).padStart(3)} ${event.type.padEnd(23)} ${event.payload.message}`));
const task = runtime.submitMission('Create and verify a mission report');
const finished = await runtime.waitForTask(task.id);
console.log(JSON.stringify(finished.result, null, 2));
if (finished.status !== 'completed') process.exitCode = 1;
