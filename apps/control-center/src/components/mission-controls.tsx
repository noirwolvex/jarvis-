"use client";

import { useRef, useState } from "react";
import { requestMissionControl, type MissionControlAction } from "@/lib/mission-control-client";
import type { Snapshot } from "@/lib/view-types";
import { Icon } from "./icons";

export function MissionControls({ snapshot, connected, refresh, interrupt }: {
  snapshot: Snapshot;
  connected: boolean;
  refresh: () => Promise<void>;
  interrupt: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);
  const paused = Boolean(snapshot.missionControl?.paused);
  const pausing = Boolean(snapshot.missionControl?.pauseRequested) && !paused;
  const confirmation = snapshot.missionControl?.pendingConfirmation;
  const active = ["EXECUTING", "RUNNING", "PAUSING", "PAUSED", "WAITING_USER"].includes(snapshot.status);
  const disabled = busy || !connected || snapshot.emergencyStopped;

  const request = async (action: MissionControlAction) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setMessage("");
    interrupt();
    try {
      await requestMissionControl(action, action === "confirm" || action === "reject" ? confirmation?.id : undefined);
      await refresh();
      setMessage(action === "pause" ? "Pause requested. The current action can finish before execution pauses." :
        action === "resume" ? "Resume requested." : action === "cancel" ? "Cancellation requested." :
        action === "confirm" ? "Approval submitted for this action." : "Rejection submitted.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Mission control request failed."); }
    finally { inFlight.current = false; setBusy(false); }
  };

  return <section className="mission-controls" aria-label="Active mission controls">
    <div className="mission-controls-row">
      <button className="button secondary" disabled={disabled || !active || Boolean(confirmation)} onClick={() => void request(paused || pausing ? "resume" : "pause")}>
        <Icon name={paused || pausing ? "play" : "pause"} size={14} />{paused ? "Resume mission" : pausing ? "Undo pause request" : "Pause mission"}
      </button>
      <button className="button secondary" disabled={disabled || !active} onClick={() => void request("cancel")}>Cancel mission</button>
      <span>{confirmation ? "Waiting for your decision" : paused ? "Paused at a safe action boundary" : pausing ? "Waiting for the current action to finish" : active ? "You can pause between actions or use Emergency stop at any time." : "Mission controls activate when execution starts."}</span>
    </div>
    {confirmation && <div className="mission-confirmation" role="alert" aria-labelledby="mission-confirmation-title">
      <strong id="mission-confirmation-title">Confirm the next action</strong>
      <p>{confirmation.summary}</p>
      {confirmation.reason && <p>{confirmation.reason}</p>}
      <span className="mono">{confirmation.tool}</span>
      {confirmation.details && <pre className="mission-action-details">{confirmation.details}</pre>}
      <div className="mission-controls-row">
        <button className="button primary" disabled={disabled} onClick={() => void request("confirm")}>Approve this action once</button>
        <button className="button secondary" disabled={disabled} onClick={() => void request("reject")}>Reject action</button>
      </div>
    </div>}
    {message && <p role="status">{message}</p>}
  </section>;
}
