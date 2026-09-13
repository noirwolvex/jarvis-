"use client";

import { useCallback, useEffect, useState } from "react";
import { initialSnapshot, type Snapshot } from "@/lib/view-types";

const finished = (status: string) => ["COMPLETED", "FAILED", "CANCELLED"].includes(status.toUpperCase());

export function HybridControlCenter() {
  const [snapshot, setSnapshot] = useState<Snapshot>({ ...initialSnapshot, mode: "hybrid" });
  const [title, setTitle] = useState("open discord app");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [connected, setConnected] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/control", { cache: "no-store", signal: AbortSignal.timeout(4500) });
      if (!response.ok) throw new Error("Control service unavailable");
      setSnapshot(await response.json() as Snapshot);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(poll, 750);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [refresh]);

  const run = async () => {
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Jarvis-Control": "hybrid" },
        body: JSON.stringify({ action: "run", title: title.trim() }),
        signal: AbortSignal.timeout(8000),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Mission rejected");
      setNotice("Real Discord launch queued. Mission completes only after a visible Discord window is verified.");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Mission request failed");
    } finally {
      setBusy(false);
    }
  };

  const latest = snapshot.tasks[0];
  const completed = snapshot.tasks.filter(task => task.status === "COMPLETED").length;

  return <div className="console">
    <div className="workspace">
      <main id="main" className="main-content">
        <div className="page-heading">
          <div>
            <div className="eyebrow"><span className="small-cross">+</span> JARVIS X / HYBRID EXECUTION</div>
            <h1>Mission control</h1>
            <p>JARVIS X plans the mission. The existing Python Windows layer performs the allowlisted local action and reports verification back.</p>
          </div>
          <div className="heading-actions">
            <span className={`connection ${connected ? "connected" : "offline"}`}><i />{connected ? "Connected" : "Disconnected"}</span>
          </div>
        </div>

        <section className="mission-launch">
          <div className="mission-intro">
            <span className="overline"><i /> REAL LOCAL EXECUTION</span>
            <h2>Old capabilities.<br /><span>New control plane.</span></h2>
            <p>The first bridged capability is deliberately narrow: launch Discord and verify that a visible Discord window exists.</p>
            <form className="mission-input" onSubmit={event => { event.preventDefault(); void run(); }}>
              <input aria-label="Hybrid mission" value={title} maxLength={160} onChange={event => setTitle(event.target.value)} placeholder="open discord app" />
              <button className="button primary" type="submit" disabled={busy || !connected || !title.trim() || snapshot.status === "EXECUTING"}>Run mission</button>
            </form>
            <div className="input-caption">Typed bridge · Discord allowlist · no shell passthrough · visible-window verification</div>
          </div>
          <div className="contract-visual">
            <div className="contract-label">CURRENT BRIDGE<span>v0.1</span></div>
            <div className="contract-step"><span className="contract-number">01</span><div><strong>Parse</strong><small>Recognize an exact Discord launch intent</small></div></div>
            <div className="contract-step"><span className="contract-number">02</span><div><strong>Execute</strong><small>Call the local Python compatibility adapter</small></div></div>
            <div className="contract-step"><span className="contract-number">03</span><div><strong>Verify</strong><small>Require a visible Discord window</small></div></div>
            <div className="contract-footer"><span className="pulse-dot" /> Mission is not complete until verification passes</div>
          </div>
        </section>

        {notice && <div className="banner notice" role="status"><span>{notice}</span></div>}

        <div className="metrics-strip">
          <div className="metric"><small>RUNTIME STATE</small><strong>{snapshot.status.toLowerCase()}</strong><span>Hybrid Python bridge</span></div>
          <div className="metric"><small>MISSIONS COMPLETED</small><strong>{String(completed).padStart(2, "0")}</strong><span>{snapshot.tasks.length} submitted</span></div>
          <div className="metric"><small>WORLD VERSION</small><strong>{String(snapshot.worldVersion).padStart(2, "0")}</strong><span>Verified local outcomes</span></div>
        </div>

        <section className="panel missions-panel">
          <div className="section-title"><div><h2>Mission history</h2><p>Real hybrid runs with explicit launch and verification state.</p></div><span className="small-pill">{snapshot.tasks.length} TOTAL</span></div>
          {snapshot.tasks.length === 0 ? <p>No hybrid missions yet.</p> : <div className="mission-table">
            {snapshot.tasks.map(task => <div className="mission-row" key={task.id}>
              <div><strong>{task.title}</strong><small>{task.summary}</small></div>
              <span className={`status ${task.status === "FAILED" ? "danger" : task.status === "RUNNING" ? "active" : ""}`}><i />{task.status.toLowerCase()}</span>
              <div><small>Plan nodes</small><strong>{task.nodes.length}</strong></div>
              <div><small>Verified</small><strong>{task.nodes.filter(node => node.status === "VERIFIED").length}/{task.nodes.length}</strong></div>
            </div>)}
          </div>}
        </section>

        {latest && <section className="panel">
          <div className="section-title"><div><h2>Latest plan</h2><p>{finished(latest.status) ? latest.summary : "Execution is still in progress."}</p></div></div>
          <div className="fact-list">{latest.nodes.map(node => <div className="fact" key={node.id}><span>{node.title}</span><code>{node.status}</code></div>)}</div>
        </section>}

        <section className="panel">
          <div className="section-title"><div><h2>Execution feed</h2><p>Newest bridge events.</p></div></div>
          <div className="fact-list">{snapshot.events.slice(-10).reverse().map(event => <div className="fact" key={event.id}><span>{event.type}</span><code>{event.summary}</code></div>)}</div>
        </section>
      </main>
    </div>
  </div>;
}
