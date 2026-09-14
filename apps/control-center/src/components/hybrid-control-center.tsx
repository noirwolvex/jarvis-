"use client";

import { useCallback, useEffect, useState } from "react";
import { ControlCenter } from "./control-center";

type AccessMode = "standard" | "full";

export function HybridControlCenter() {
  const [mode, setMode] = useState<AccessMode>("standard");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [allowShell, setAllowShell] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/full-access", { cache: "no-store", signal: AbortSignal.timeout(4000) });
      if (!response.ok) return;
      const body = await response.json() as { mode?: AccessMode };
      if (body.mode === "standard" || body.mode === "full") setMode(body.mode);
    } catch { /* ControlCenter handles primary connection state. */ }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 1500);
    return () => clearInterval(timer);
  }, [refresh]);

  const changeMode = async (next: AccessMode) => {
    if (next === "full") {
      const confirmed = window.confirm(
        "Enable Full Access for this local JARVIS session?\n\n" +
        "JARVIS will be able to perform general desktop actions. Screen images may be sent to your configured model provider. Ctrl+Alt+Escape stops the worker.\n\n" +
        (allowShell ? "Terminal execution is also enabled. Commands run as your Windows account and can modify files and system settings accessible to it." : "Terminal execution remains disabled. Other high-risk actions remain separately gated.")
      );
      if (!confirmed) return;
    }
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/full-access", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Jarvis-Control": "hybrid" },
        body: JSON.stringify({ mode: next, ...(next === "full" ? { allowShell, confirmation: allowShell ? "ENABLE_FULL_ACCESS_AND_SHELL" : "ENABLE_FULL_ACCESS" } : {}) }),
        signal: AbortSignal.timeout(6000),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Access mode change failed");
      setMode(next);
      setMessage(next === "full" ? "Full Access enabled for this local session." : "Standard mode restored.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Access mode change failed");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    try {
      const response = await fetch("/api/control", { method: "POST",
        headers: { "Content-Type": "application/json", "X-Jarvis-Control": "hybrid" },
        body: JSON.stringify({ action: "stop" }), signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error("Stop request failed; use Ctrl+Alt+Escape");
      setMode("standard");
      setMessage("Emergency stop latched. Reset it before enabling Full Access again.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Use Ctrl+Alt+Escape to stop"); }
  };

  return <>
    <div style={{
      position: "fixed", right: 18, bottom: 18, zIndex: 2000, width: 310,
      padding: 14, borderRadius: 14, border: "1px solid rgba(255,255,255,.15)",
      background: "rgba(9,12,18,.94)", backdropFilter: "blur(18px)", boxShadow: "0 18px 60px rgba(0,0,0,.45)",
      color: "#f5f7fb", fontFamily: "inherit",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontSize: 11, letterSpacing: ".12em", opacity: .65 }}>ACCESS MODE</div>
          <strong style={{ fontSize: 15 }}>{mode === "full" ? "FULL ACCESS" : "STANDARD"}</strong>
        </div>
        <span style={{ width: 10, height: 10, borderRadius: 999, background: mode === "full" ? "#ffb020" : "#47d18c" }} />
      </div>
      <p style={{ fontSize: 12, lineHeight: 1.45, opacity: .75, margin: "10px 0" }}>
        {mode === "full" ? "General desktop missions are enabled for this local session." : "Standard hybrid restrictions are active."}
      </p>
      {mode === "standard" && <label style={{ display: "block", fontSize: 12, marginBottom: 10 }}>
        <input type="checkbox" checked={allowShell} onChange={event => setAllowShell(event.target.checked)} /> Allow terminal commands when enabling
      </label>}
      <button
        type="button"
        disabled={busy}
        onClick={() => void changeMode(mode === "full" ? "standard" : "full")}
        style={{ width: "100%", border: 0, borderRadius: 9, padding: "9px 12px", fontWeight: 700, cursor: busy ? "wait" : "pointer" }}
      >
        {busy ? "Updating…" : mode === "full" ? "Disable Full Access" : "Enable Full Access"}
      </button>
      <button type="button" onClick={() => void stop()} style={{ width: "100%", marginTop: 8, background: "#b42335", color: "white", border: 0, borderRadius: 9, padding: 10, cursor: "pointer", fontWeight: 700 }}>Emergency stop · Ctrl+Alt+Esc</button>
      {message && <div style={{ fontSize: 11, marginTop: 8, opacity: .8 }}>{message}</div>}
    </div>
    <ControlCenter />
  </>;
}
