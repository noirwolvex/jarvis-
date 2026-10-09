import test from "node:test";
import assert from "node:assert/strict";
import { MissionVoice } from "../src/lib/mission-voice.ts";
import { requestMissionControl } from "../src/lib/mission-control-client.ts";
import type { EventView } from "../src/lib/view-types.ts";

const epoch = 1_800_000_000_000;
const approvalId = "abcd".repeat(8);
function event(sequence: number, type: string, taskId = "task-one", timestamp = epoch): EventView {
  return { id: `event-${sequence}`, sequence, timestamp: new Date(timestamp).toISOString(), type, taskId,
    summary: "Private recipient, password, and tool arguments must never be spoken." };
}
function voiceFixture() {
  const spoken: string[] = [];
  let cancellations = 0;
  let now = epoch;
  const voice = new MissionVoice({ speak: text => spoken.push(text), cancel: () => cancellations++ }, () => now);
  return { voice, spoken, advance: (ms: number) => now += ms, cancellations: () => cancellations };
}

test("voice is opt-in and never replays history when enabled or re-enabled", () => {
  const f = voiceFixture();
  const history = [event(1, "TASK_COMPLETED")];
  f.voice.update(history);
  assert.deepEqual(f.spoken, []);
  f.voice.enable(history);
  f.voice.update(history);
  assert.deepEqual(f.spoken, ["Voice feedback enabled."]);
  f.voice.disable();
  f.voice.update([...history, event(2, "TASK_FAILED")]);
  f.voice.enable([...history, event(2, "TASK_FAILED")]);
  f.voice.update([...history, event(2, "TASK_FAILED")]);
  assert.deepEqual(f.spoken, ["Voice feedback enabled.", "Voice feedback enabled."]);
});

test("voice coalesces rapid changes and announces a verified completion exactly once", () => {
  const f = voiceFixture();
  f.voice.enable([]);
  const events = [event(1, "TASK_CREATED"), event(2, "ACTION_STARTED"), event(3, "TOOL_RESULT"), event(4, "TASK_COMPLETED")];
  f.voice.update(events);
  f.voice.update(events);
  assert.deepEqual(f.spoken, ["Voice feedback enabled.", "Mission completed and verified."]);
  assert.equal(f.cancellations(), 2);
});

test("raw tools, progress, and private text never enter the speech output", () => {
  const f = voiceFixture();
  f.voice.enable([]);
  f.voice.update([event(1, "TOOL_RESULT"), event(2, "AGENT_PROGRESS"), event(3, "SCREEN_OBSERVED")]);
  f.voice.update([event(4, "USER_ACTION_REQUIRED")]);
  assert.deepEqual(f.spoken, ["Voice feedback enabled.", "Your attention is required. Check the mission controls."]);
  assert.ok(f.spoken.every(text => !text.includes("Private")));
});

test("stop cancels prior speech, overrides terminal events, and is not repeated by polling", () => {
  const f = voiceFixture();
  f.voice.enable([]);
  f.voice.update([event(1, "ACTION_STARTED")]);
  f.voice.update([event(2, "EMERGENCY_STOP"), event(3, "TASK_FAILED")], true);
  f.voice.update([event(2, "EMERGENCY_STOP"), event(3, "TASK_FAILED")], true);
  assert.equal(f.spoken.at(-1), "Emergency stop activated.");
  assert.equal(f.spoken.filter(text => text.includes("Emergency")).length, 1);
  assert.ok(!f.spoken.some(text => text.includes("could not finish")));
  const before = f.cancellations();
  f.voice.interrupt();
  assert.equal(f.cancellations(), before + 1);
});

test("progress is throttled but pause, confirmation, and errors are immediate", () => {
  const f = voiceFixture();
  f.voice.enable([]);
  f.voice.update([event(1, "ACTION_STARTED")]);
  f.voice.update([event(2, "RECOVERY")]);
  assert.equal(f.spoken.at(-1), "Mission started.");
  f.voice.update([event(3, "ACTION_PAUSED")]);
  assert.equal(f.spoken.at(-1), "Mission paused.");
  f.voice.update([event(4, "CONFIRMATION_REQUIRED")]);
  assert.match(f.spoken.at(-1)!, /confirmation/);
  f.voice.update([event(5, "TASK_FAILED")]);
  assert.match(f.spoken.at(-1)!, /could not finish/);
  f.advance(4000);
  f.voice.update([event(6, "RECOVERY")]);
  assert.match(f.spoken.at(-1)!, /Recovering/);
});

test("long-disconnected history is not spoken and restarted event IDs remain usable", () => {
  const f = voiceFixture();
  f.voice.enable([event(1, "ACTION_STARTED")]);
  f.advance(31_000);
  f.voice.update([event(2, "TASK_COMPLETED")]);
  assert.equal(f.spoken.length, 1);
  f.voice.update([event(1, "TASK_FAILED", "new-task", epoch + 31_000)]);
  assert.match(f.spoken.at(-1)!, /could not finish/);
});

test("mission control sends one explicit approval bound to the displayed request ID", async () => {
  const calls: { url: unknown; options?: RequestInit }[] = [];
  const request: typeof fetch = async (url, options) => {
    calls.push({ url, options });
    return Response.json({ ok: true }, { status: 202 });
  };
  await requestMissionControl("confirm", approvalId, request);
  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.url, "/api/mission-control");
  assert.deepEqual(JSON.parse(String(calls[0]?.options?.body)), { action: "confirm", confirmationId: approvalId });
  assert.deepEqual(calls[0]?.options?.headers, { "Content-Type": "application/json", "X-Jarvis-Control": "hybrid" });
  assert.ok(calls[0]?.options?.signal instanceof AbortSignal);
});

test("missing confirmations cannot issue requests and rejected requests are never retried", async () => {
  let calls = 0;
  const request: typeof fetch = async () => {
    calls++;
    return new Response(JSON.stringify({ error: "Confirmation expired" }), { status: 409 });
  };
  await assert.rejects(requestMissionControl("confirm", undefined, request), /current confirmation/);
  await assert.rejects(requestMissionControl("reject", " ", request), /current confirmation/);
  assert.equal(calls, 0);
  await assert.rejects(requestMissionControl("confirm", approvalId, request), /Confirmation expired/);
  assert.equal(calls, 1);
});

test("pause, resume and cancel do not carry an approval grant", async () => {
  const bodies: unknown[] = [];
  const request: typeof fetch = async (_url, options) => {
    bodies.push(JSON.parse(String(options?.body)));
    return Response.json({ ok: true }, { status: 202 });
  };
  await requestMissionControl("pause", undefined, request);
  await requestMissionControl("resume", undefined, request);
  await requestMissionControl("cancel", undefined, request);
  for (const action of ["pause", "resume", "cancel"] as const) {
    await assert.rejects(requestMissionControl(action, approvalId, request), /cannot also grant/);
  }
  assert.deepEqual(bodies, [{ action: "pause" }, { action: "resume" }, { action: "cancel" }]);
});

test("mission control never retries or treats malformed success responses as approval", async () => {
  for (const response of [new Response("<html>Error</html>"), Response.json(null), Response.json({ ok: false }), Response.json({})]) {
    let calls = 0;
    await assert.rejects(requestMissionControl("confirm", approvalId, async () => { calls++; return response; }), /Refresh the mission state/);
    assert.equal(calls, 1);
  }
  let calls = 0;
  const failedRequest: typeof fetch = async () => { calls++; throw new Error("Network interrupted"); };
  await assert.rejects(requestMissionControl("confirm", approvalId, failedRequest), /Network interrupted/);
  assert.equal(calls, 1);
});

test("voice rejects malformed or far-future timestamps and reports cooperative cancellation", () => {
  const f = voiceFixture();
  f.voice.enable([]);
  f.voice.update([{ ...event(1, "TASK_COMPLETED"), timestamp: "invalid" }, event(2, "TASK_COMPLETED", "task-one", epoch + 60_000)]);
  assert.deepEqual(f.spoken, ["Voice feedback enabled."]);
  f.voice.update([event(3, "TASK_CANCELLED")]);
  assert.equal(f.spoken.at(-1), "Mission cancelled.");
  assert.ok(!f.spoken.includes("Emergency stop activated."));
});
