import type { EventView } from "./view-types";

export interface VoiceOutput {
  speak(text: string): void;
  cancel(): void;
}

const announcements: Record<string, string> = {
  TASK_CREATED: "Mission queued.",
  ACTION_STARTED: "Mission started.",
  TASK_RESUMED: "Mission resumed.",
  ACTION_RESUMED: "Mission resumed.",
  TASK_PAUSED: "Mission paused.",
  ACTION_PAUSED: "Mission paused.",
  USER_ACTION_REQUIRED: "Your attention is required. Check the mission controls.",
  CONFIRMATION_REQUIRED: "An action needs your confirmation. Check the mission controls.",
  RECOVERY: "Recovering from an unexpected state.",
  TASK_COMPLETED: "Mission completed and verified.",
  TASK_FAILED: "The mission could not finish. Check the execution timeline.",
  TASK_CANCELLED: "Mission cancelled.",
  EMERGENCY_STOP: "Emergency stop activated.",
};

/** Speaks only fixed status labels, never screen content, tool arguments, or mission text. */
export class MissionVoice {
  private enabled = false;
  private seen = new Set<string>();
  private lastKey = "";
  private lastProgressAt = -Infinity;
  private stopped = false;

  constructor(private readonly output: VoiceOutput, private readonly now = Date.now) {}

  enable(events: readonly EventView[]) {
    this.enabled = true;
    this.baseline(events);
    this.lastKey = "";
    this.lastProgressAt = -Infinity;
    this.stopped = false;
    this.say("Voice feedback enabled.");
  }

  disable() { this.enabled = false; this.interrupt(); }
  interrupt() { this.output.cancel(); }
  baseline(events: readonly EventView[]) { this.seen = new Set(events.map(event => `${event.id}:${event.timestamp}`)); }

  update(events: readonly EventView[], emergencyStopped = false) {
    const now = this.now();
    const fresh = events.filter(event => {
      const age = now - Date.parse(event.timestamp);
      return !this.seen.has(`${event.id}:${event.timestamp}`) && Number.isFinite(age)
        && age >= -5000 && age <= 30_000;
    });
    this.baseline(events);
    if (!this.enabled) return;
    if (emergencyStopped) {
      if (!this.stopped) this.say(announcements.EMERGENCY_STOP!);
      this.stopped = true;
      return;
    }
    this.stopped = false;
    // Coalesce a poll into its latest meaningful status instead of building a speech backlog.
    const event = fresh.filter(item => announcements[item.type]).at(-1);
    if (!event) return;
    const text = announcements[event.type]!;
    const key = `${event.taskId}:${text}`;
    if (key === this.lastKey) return;
    const progress = ["TASK_CREATED", "ACTION_STARTED", "RECOVERY"].includes(event.type);
    if (progress && this.now() - this.lastProgressAt < 4000) return;
    if (progress) this.lastProgressAt = this.now();
    this.lastKey = key;
    this.say(text);
  }

  private say(text: string) {
    this.output.cancel();
    this.output.speak(text);
  }
}
