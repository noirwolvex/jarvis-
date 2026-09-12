"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { initialSnapshot, type Snapshot } from "@/lib/view-types";

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const formatBytes = (value: number) => value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`;

export function NativeControlCenter() {
  const [snapshot, setSnapshot] = useState<Snapshot>(initialSnapshot);
  const [connection, setConnection] = useState<"connecting" | "connected" | "offline">("connecting");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("Native mode selected. Waiting for the Rust execution daemon…");
  const [x, setX] = useState(0);
  const [y, setY] = useState(0);
  const [text, setText] = useState("");

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/control", { cache: "no-store", signal: AbortSignal.timeout(4500) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Native control service unavailable");
      setSnapshot(body as Snapshot);
      setConnection("connected");
      return body as Snapshot;
    } catch (error) {
      setConnection("offline");
      setNotice(error instanceof Error ? error.message : "Native control service unavailable");
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [refresh]);

  const post = useCallback(async (action: string, payload: Record<string, unknown> = {}) => {
    const response = await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jarvis-Control": "native" },
      body: JSON.stringify({ action, ...payload }),
      signal: AbortSignal.timeout(10000),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Native action was rejected");
    await refresh();
    return body;
  }, [refresh]);

  const run = useCallback(async (action: string, payload: Record<string, unknown> = {}) => {
    setBusy(true);
    try {
      await post(action, payload);
      setNotice(action === "capture" ? "Fresh native screen evidence captured." : action === "stop" ? "Emergency stop latched. Restart the daemon locally before further execution." : "Native action accepted by the Rust daemon.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Native action failed");
    } finally { setBusy(false); }
  }, [post]);

  const delayed = useCallback(async (action: "click" | "type", payload: Record<string, unknown>) => {
    setBusy(true);
    try {
      for (const seconds of [3, 2, 1]) {
        setNotice(`${action === "click" ? "Click" : "Keyboard input"} will execute in ${seconds}s. Switch to the intended target window now.`);
        await sleep(1000);
      }
      await post(action, payload);
      setNotice("Foreground-bound native input accepted. The daemon rejected it automatically if the foreground changed.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Native action failed");
    } finally { setBusy(false); }
  }, [post]);

  const native = snapshot.native;
  const capture = native?.capture;
  const facts = useMemo(() => Object.fromEntries(snapshot.facts.map(fact => [fact.key, fact.value])), [snapshot.facts]);

  const pickCoordinate = (event: React.MouseEvent<HTMLImageElement>) => {
    if (!capture) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const relativeX = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const relativeY = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    setX(Math.round(capture.display.x + relativeX * (capture.display.width - 1)));
    setY(Math.round(capture.display.y + relativeY * (capture.display.height - 1)));
    setNotice("Coordinates selected from the preview. Use the 3-second delayed click so you can switch to the intended target window.");
  };

  return <main className="native-shell">
    <header className="native-header">
      <div><span className="eyebrow">JARVIS X / NATIVE EXECUTION</span><h1>Rust Control Center</h1><p>Mutual-TLS loopback control with typed actions, bounded screen evidence, foreground-bound input, and an independent emergency stop.</p></div>
      <div className="header-actions"><span className={`connection ${connection}`}><i />{connection}</span><button className="stop" disabled={busy || snapshot.emergencyStopped || connection !== "connected"} onClick={() => void run("stop")}>EMERGENCY STOP</button></div>
    </header>

    {notice && <div className="notice">{notice}</div>}
    {snapshot.emergencyStopped && <div className="emergency">Emergency stop is latched. Native execution remains blocked until the Rust daemon is restarted locally.</div>}

    <section className="status-grid">
      <article><span>DAEMON STATE</span><strong>{snapshot.status}</strong><small>{native?.daemonSimulation ? "daemon simulation" : "native execution"}</small></article>
      <article><span>NATIVE INPUT</span><strong>{native?.nativeInput ? "ENABLED" : "DISABLED"}</strong><small>foreground + fresh-frame bound</small></article>
      <article><span>FOREGROUND PID</span><strong>{native?.foreground?.process_id ?? "—"}</strong><small>{native?.foreground?.title || "No foreground binding reported"}</small></article>
      <article><span>CAPTURE RING</span><strong>{facts["daemon.capture_ring_frames"] ?? "0"}</strong><small>{formatBytes(Number(facts["daemon.capture_ring_bytes"] ?? 0))} retained</small></article>
    </section>

    <section className="workspace-grid">
      <article className="panel screen-panel">
        <div className="panel-head"><div><span className="eyebrow">LIVE VISION</span><h2>Native screen evidence</h2></div><button disabled={busy || connection !== "connected" || snapshot.emergencyStopped} onClick={() => void run("capture")}>Capture now</button></div>
        <div className="screen-stage">
          {capture ? <img src={capture.previewDataUrl} alt="Bounded native desktop preview" onClick={pickCoordinate} /> : <div className="empty"><strong>No native frame yet</strong><span>Capture a frame from the Rust daemon to inspect the current display.</span></div>}
        </div>
        {capture && <div className="frame-meta"><span>Frame <code>{capture.frameId.slice(0, 8)}</code></span><span>{capture.display.width}×{capture.display.height} @ {capture.display.scale.toFixed(2)}x</span><span>SHA-256 <code>{capture.sha256.slice(0, 12)}…</code></span></div>}
      </article>

      <article className="panel input-panel">
        <span className="eyebrow">TYPED INPUT</span><h2>Mouse + keyboard channel</h2><p>Input executes only when the foreground process/title still matches the binding observed by the daemon. A fresh frame is captured immediately before execution.</p>
        <div className="field-row"><label>X<input type="number" value={x} onChange={event => setX(Number(event.target.value))} /></label><label>Y<input type="number" value={y} onChange={event => setY(Number(event.target.value))} /></label></div>
        <button className="primary" disabled={busy || !native?.nativeInput || snapshot.emergencyStopped || connection !== "connected"} onClick={() => void delayed("click", { x, y })}>Click in 3 seconds</button>
        <label className="text-field">Text<textarea value={text} maxLength={4096} onChange={event => setText(event.target.value)} placeholder="Text to enter into the foreground application…" /></label>
        <button className="primary" disabled={busy || !native?.nativeInput || !text || snapshot.emergencyStopped || connection !== "connected"} onClick={() => void delayed("type", { text })}>Type in 3 seconds</button>
        <div className="guard-list"><span>✓ No shell command input</span><span>✓ Frame freshness checked</span><span>✓ Foreground PID + title checked</span><span>✓ Capability scope checked</span><span>✓ Emergency latch checked between input stages</span></div>
      </article>
    </section>

    <section className="panel events-panel"><div className="panel-head"><div><span className="eyebrow">AUDIT WINDOW</span><h2>Native execution events</h2></div><span>{snapshot.events.length} retained</span></div><div className="events">{snapshot.events.length ? snapshot.events.slice().reverse().map(event => <div className="event" key={event.id}><time>{new Date(event.timestamp).toLocaleTimeString("en-GB", { hour12: false })}</time><strong>{event.type}</strong><span>{event.summary}</span></div>) : <div className="empty small"><strong>No native actions yet</strong><span>Capture or execute a typed action to populate the local event window.</span></div>}</div></section>

    <style jsx>{`
      :global(body){margin:0;background:#06090f;color:#eaf3ff;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.native-shell{min-height:100vh;padding:34px;box-sizing:border-box;background:radial-gradient(circle at 75% 5%,rgba(92,181,255,.12),transparent 30%),#06090f}.native-header{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;max-width:1480px;margin:0 auto 20px}.eyebrow{font-size:11px;letter-spacing:.18em;color:#72bfff;font-weight:800}.native-header h1{font-size:36px;margin:7px 0 6px}.native-header p{margin:0;max-width:820px;color:#8fa3bd;line-height:1.6}.header-actions{display:flex;gap:12px;align-items:center}.connection{display:flex;gap:8px;align-items:center;padding:9px 12px;border:1px solid #1c2b3e;border-radius:10px;background:#0b111b;text-transform:capitalize;font-size:13px}.connection i{width:7px;height:7px;border-radius:50%;background:#74859a}.connection.connected i{background:#55dfa6;box-shadow:0 0 12px #55dfa6}.connection.offline i{background:#ff6d75}.stop{background:#3a1117;color:#ff98a0;border:1px solid #6b202b;border-radius:10px;padding:11px 15px;font-weight:800}.stop:disabled,button:disabled{opacity:.45;cursor:not-allowed}.notice,.emergency{max-width:1450px;margin:0 auto 18px;padding:12px 15px;border-radius:10px;border:1px solid #1e3650;background:#0b1724;color:#b9d9f6}.emergency{border-color:#692532;background:#260e14;color:#ffadb3}.status-grid{max-width:1480px;margin:0 auto 18px;display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.status-grid article,.panel{background:linear-gradient(180deg,#0d131e,#090e16);border:1px solid #182638;border-radius:14px;box-shadow:0 14px 40px rgba(0,0,0,.18)}.status-grid article{padding:17px}.status-grid span{display:block;color:#6f849e;font-size:10px;letter-spacing:.14em}.status-grid strong{display:block;margin:8px 0 4px;font-size:20px}.status-grid small{color:#8093a9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}.workspace-grid{max-width:1480px;margin:0 auto 18px;display:grid;grid-template-columns:minmax(0,1.7fr) minmax(320px,.8fr);gap:16px}.panel{padding:18px}.panel-head{display:flex;justify-content:space-between;gap:15px;align-items:center}.panel h2{margin:6px 0 8px}.panel p{color:#8ba0b8;line-height:1.55}.panel button{background:#7dc7ff;color:#05101c;border:0;border-radius:9px;padding:10px 14px;font-weight:800}.screen-stage{margin-top:14px;min-height:420px;border-radius:11px;border:1px solid #15283d;background:#04070b;display:flex;align-items:center;justify-content:center;overflow:hidden}.screen-stage img{width:100%;height:100%;max-height:650px;object-fit:contain;cursor:crosshair;image-rendering:auto}.empty{display:flex;flex-direction:column;gap:7px;text-align:center;color:#6f849e}.empty strong{color:#b6c7da}.empty.small{padding:26px}.frame-meta{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;color:#7188a1;font-size:12px}.frame-meta code{color:#a8d7ff}.input-panel{display:flex;flex-direction:column}.field-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}.field-row label,.text-field{color:#8095ad;font-size:12px;font-weight:700}.field-row input,.text-field textarea{box-sizing:border-box;width:100%;margin-top:6px;background:#060b12;color:#eef6ff;border:1px solid #1a2b40;border-radius:9px;padding:10px;outline:none}.text-field{margin-top:16px}.text-field textarea{min-height:120px;resize:vertical}.primary{margin-top:8px}.guard-list{display:grid;gap:8px;margin-top:18px;padding-top:15px;border-top:1px solid #152334;color:#7794af;font-size:12px}.events-panel{max-width:1444px;margin:0 auto}.events{display:grid;margin-top:10px}.event{display:grid;grid-template-columns:90px 170px 1fr;gap:12px;padding:10px 0;border-top:1px solid #142233;font-size:12px}.event time{color:#60768f}.event strong{color:#9ccfff}.event span{color:#879caf}@media(max-width:900px){.native-shell{padding:18px}.native-header{flex-direction:column}.status-grid{grid-template-columns:1fr 1fr}.workspace-grid{grid-template-columns:1fr}.screen-stage{min-height:250px}.event{grid-template-columns:70px 1fr}.event span{grid-column:1/-1}}@media(max-width:560px){.status-grid{grid-template-columns:1fr}.header-actions{width:100%;justify-content:space-between}.native-header h1{font-size:29px}}
    `}</style>
  </main>;
}
