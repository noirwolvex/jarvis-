"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./icons";
import { initialSnapshot, type EventView, type PageId, type Snapshot, type TaskView } from "@/lib/view-types";

const navigation: { id: PageId; name: string; group: string }[] = [
  { id: "mission", name: "Mission control", group: "WORKSPACE" },
  { id: "vision", name: "Live vision", group: "WORKSPACE" },
  { id: "graph", name: "Task graph", group: "WORKSPACE" },
  { id: "timeline", name: "Execution timeline", group: "WORKSPACE" },
  { id: "memory", name: "Memory", group: "INTELLIGENCE" },
  { id: "models", name: "Model center", group: "INTELLIGENCE" },
  { id: "security", name: "Security & policies", group: "SYSTEM" },
  { id: "resources", name: "Resources", group: "SYSTEM" },
  { id: "recovery", name: "Recovery", group: "SYSTEM" }
];
const formatStatus = (s: string) => s.toLowerCase().replaceAll("_", " ");
const time = (s: string) => s ? new Date(s).toLocaleTimeString("en-GB", { hour12: false }) : "—";
const isFinished = (s: string) => ["COMPLETED", "SUCCEEDED", "VERIFIED"].includes(s.toUpperCase());

function Status({ value, dot = true }: { value: string; dot?: boolean }) {
  const danger = /STOP|ERROR|FAIL|DENIED/.test(value);
  const active = /EXECUT|RUNNING|VERIFY|PLAN/.test(value);
  return <span className={`status ${danger ? "danger" : active ? "active" : ""}`}>{dot && <i />}{formatStatus(value)}</span>;
}

function SectionTitle({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return <div className="section-title"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>;
}

export function ControlCenter() {
  const [page, setPage] = useState<PageId>("mission");
  const [snapshot, setSnapshot] = useState<Snapshot>(initialSnapshot);
  const [connection, setConnection] = useState<"connecting" | "connected" | "offline">("connecting");
  const [title, setTitle] = useState("Prepare a verified workspace report");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [palette, setPalette] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [light, setLight] = useState(false);
  const [selectedNode, setSelectedNode] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const paletteRef = useRef<HTMLInputElement>(null);
  const paletteDialogRef = useRef<HTMLDivElement>(null);
  const commandButtonRef = useRef<HTMLButtonElement>(null);
  const latest = snapshot.tasks.find(task => task.id === selectedTaskId) ?? snapshot.tasks[0];
  const completed = snapshot.tasks.filter(t => isFinished(t.status)).length;
  const verified = snapshot.events.filter(e => /VERIFICATION_COMPLETED|ACTION_VERIFIED/.test(e.type)).length;
  const halted = snapshot.emergencyStopped;
  const hybrid = snapshot.mode === "hybrid";

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/control", { cache: "no-store", signal: AbortSignal.timeout(4500) });
      if (!response.ok) throw new Error("Control service unavailable");
      setSnapshot(await response.json() as Snapshot);
      setConnection("connected");
    } catch { setConnection("offline"); }
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

  useEffect(() => {
    try { setLight(localStorage.getItem("jarvis-theme") === "light"); } catch { /* Storage is optional. */ }
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setPalette(p => !p); setPaletteQuery("");
      }
      if (event.key === "Escape") { setPalette(false); setMobileNav(false); commandButtonRef.current?.focus(); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  useEffect(() => { if (palette) paletteRef.current?.focus(); }, [palette]);

  const navigate = (id: PageId) => { setPage(id); setMobileNav(false); setSearch(""); setPalette(false); };
  const control = async (action: string) => {
    setBusy(true); setNotice("");
    try {
      const response = await fetch("/api/control", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis-Control": hybrid ? "hybrid" : "simulation" },
        body: JSON.stringify({ action, ...(action === "run" ? { title: title.trim() } : {}) }),
        signal: AbortSignal.timeout(8000)
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "The action could not be completed.");
      await refresh();
      setNotice(action === "run" ? "Mission queued. Follow each action in the execution timeline." : action === "stop" ? "Emergency stop requested. Explicit reset is required before new work." : "Control state updated.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Control request failed."); }
    finally { setBusy(false); }
  };

  const exportEvents = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ mode: snapshot.mode, exportedAt: new Date().toISOString(), events: snapshot.events }, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "jarvis-execution-events.json"; anchor.click(); URL.revokeObjectURL(url);
    setNotice("Current event window exported as JSON.");
  };

  return <div className={`console ${light ? "light" : ""}`}>
    <a href="#main" className="skip-link">Skip to workspace</a>
    {mobileNav && <button className="nav-shade" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
      <a className="brand" href="#main" onClick={() => navigate("mission")} aria-label="JARVIS X home"><span className="brand-symbol"><span /></span><span>JARVIS<span className="brand-x">X</span><small>OPERATING FABRIC</small></span></a>
      <div className="workspace-select"><div className="workspace-icon"><Icon name="terminal" size={18} /></div><div>Local workspace<small>{hybrid ? "Desktop execution" : "Reference environment"}</small></div><span className="tiny-tag">01</span></div>
      <nav aria-label="Main navigation">{["WORKSPACE", "INTELLIGENCE", "SYSTEM"].map(group => <div className="nav-group" key={group}><p>{group}</p>{navigation.filter(n => n.group === group).map(n => <button key={n.id} className={`nav-item ${page === n.id ? "selected" : ""}`} aria-current={page === n.id ? "page" : undefined} onClick={() => navigate(n.id)}><Icon name={n.id} /><span>{n.name}</span>{n.id === "mission" && <span className="nav-count">{snapshot.tasks.length.toString().padStart(2, "0")}</span>}</button>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="environment"><span className="environment-dot" /><div>{hybrid ? "Desktop agent" : "Simulation environment"}<small>{hybrid ? "Explicit local access" : "Local · No native actions"}</small></div><Icon name="security" size={16} /></div><button className="profile" onClick={() => navigate("security")}><span className="avatar">OP</span><span>Local operator<small>Workspace administrator</small></span><Icon name="chevron" size={16} /></button></div>
    </aside>

    <div className="workspace">
      <header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-toggle" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Icon name="menu" /></button><Icon name="layers" size={17} /><span>Workspace</span><span className="slash">/</span><strong>{navigation.find(n => n.id === page)?.name}</strong></div><div className="topbar-actions"><button ref={commandButtonRef} className="command-search" onClick={() => { setPalette(true); setPaletteQuery(""); }}><Icon name="search" size={15} /><span>Jump to anything…</span><kbd>Ctrl K</kbd></button><span className={`connection ${connection}`}><i />{connection === "connected" ? "Connected" : connection === "offline" ? "Disconnected" : "Connecting"}</span><button className="icon-button" title={light ? "Use dark theme" : "Use light theme"} aria-label={light ? "Use dark theme" : "Use light theme"} onClick={() => { setLight(!light); try { localStorage.setItem("jarvis-theme", light ? "dark" : "light"); } catch { /* optional */ } }}><Icon name={light ? "moon" : "sun"} /></button></div></header>

      <main id="main" className="main-content">
        <div className="page-heading"><div><div className="eyebrow"><span className="small-cross">+</span> JARVIS X / CONTROL CENTER</div><h1>{navigation.find(n => n.id === page)?.name}</h1><p>{page === "mission" ? "Intelligence proposes. Evidence confirms. You stay in control." : subtitles[page]}</p></div><div className="heading-actions"><button className="button secondary" disabled={hybrid || busy || halted || connection !== "connected"} onClick={() => void control(snapshot.status === "PAUSED" ? "resume" : "pause")}><Icon name={snapshot.status === "PAUSED" ? "play" : "pause"} size={14} />{snapshot.status === "PAUSED" ? "Resume" : "Pause"}</button><button className="button stop-button" disabled={halted} onClick={() => void control("stop")}><Icon name="stop" size={13} />{hybrid ? "Emergency stop" : "Stop simulation"}</button></div></div>

        {connection === "offline" && <div className="banner warning" role="alert"><Icon name="info" /><span>Connection lost. The last received state may be stale. Controls are disabled until the service reconnects.</span><button onClick={() => void refresh()}>Reconnect</button></div>}
        {halted && <div className="banner emergency" role="alert"><Icon name="stop" /><span>Emergency stop is latched. Full Access must be enabled again after reset.</span><button disabled={busy || connection !== "connected"} onClick={() => void control("reset")}>Reset emergency stop</button></div>}
        {notice && <div className="banner notice" role="status"><Icon name="info" /><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice("")}><Icon name="close" size={15} /></button></div>}

        {page === "mission" && <>
          <section className="mission-launch">
            <div className="mission-intro"><span className="overline"><i /> VERIFIED EXECUTION, BY DESIGN</span><h2>A clear objective.<br /><span>A verified outcome.</span></h2><p>Explore a complete mission, from the first observation<br className="desktop-break" /> to the final evidence-backed state.</p><form className="mission-input" onSubmit={e => { e.preventDefault(); void control("run"); }}><Icon name="terminal" size={18} /><input aria-label="Mission objective" value={title} maxLength={hybrid ? 8000 : 160} onChange={e => setTitle(e.target.value)} placeholder={hybrid ? "Describe the task and how to verify it…" : "Give this simulation a name…"} /><button className="button primary" type="submit" disabled={busy || halted || !title.trim() || connection !== "connected"}><Icon name="play" size={13} />{hybrid ? "Run mission" : "Run simulation"}</button></form><div className="input-caption"><Icon name="security" size={12} /> {hybrid ? "Observed actions · Saved checkpoints · Explicit device permissions" : "Fixed workspace-report workflow · In-memory files · No model required"}</div></div>
            <div className="contract-visual"><div className="contract-label">THE EXECUTION CONTRACT<span>v0.1</span></div>{[{ n: "01", title: "Observe", text: "Ground every decision in current state", icon: "vision" }, { n: "02", title: "Authorize", text: "Check policy, scope, and freshness", icon: "security" }, { n: "03", title: "Execute", text: "Apply one bounded, typed action", icon: "terminal" }, { n: "04", title: "Verify", text: "Confirm the outcome before commit", icon: "check" }].map((step, index) => <div className="contract-step" key={step.n}><span className="contract-number">{step.n}</span><div className={`contract-icon ${index === 3 ? "last" : ""}`}><Icon name={step.icon} size={18} /></div><div><strong>{step.title}</strong><small>{step.text}</small></div>{index === 3 && <span className="contract-end">↗</span>}</div>)}<div className="contract-footer"><span className="pulse-dot" /> No state commit without verification</div></div>
          </section>

          <div className="metrics-strip"><Metric label="RUNTIME STATE" value={formatStatus(snapshot.status)} detail={hybrid ? "Local desktop worker" : "Local simulation"} icon="resources" /><Metric label="MISSIONS COMPLETED" value={String(completed).padStart(2, "0")} detail={`${snapshot.tasks.length} submitted in this session`} icon="check" /><Metric label="VERIFICATION EVENTS" value={String(verified).padStart(2, "0")} detail="Observed outcome checks" icon="security" /><Metric label="EVENTS RETAINED" value={String(snapshot.events.length).padStart(2, "0")} detail="Bounded, session event window" icon="timeline" /></div>

          <div className="mission-grid"><section className="panel vision-panel"><SectionTitle title="Computer view" subtitle={hybrid ? "Latest observed desktop state" : "A view into the simulated workspace"} action={<button className="text-button" onClick={() => navigate("vision")}>Inspect <Icon name="external" size={13} /></button>} /><ComputerView snapshot={snapshot} /><div className="vision-footer"><span><i /> {hybrid ? "LATEST OBSERVATION" : "SYNTHETIC SCENE"}</span><span>World version <b>{snapshot.worldVersion}</b></span><span>Source <b>{hybrid ? "Python screen capture" : "simulation adapter"}</b></span></div></section><section className="panel activity-panel"><SectionTitle title="Execution feed" action={<span className="small-pill">SESSION</span>} /><EventList events={snapshot.events.slice(-5).reverse()} compact /><button className="panel-link" onClick={() => navigate("timeline")}>Open execution timeline<Icon name="arrow" size={15} /></button></section></div>

          <section className="panel missions-panel"><SectionTitle title="Mission history" subtitle="Every run has an objective, a plan, and a trace." action={<span className="small-pill">{snapshot.tasks.length} TOTAL</span>} /><MissionTable tasks={snapshot.tasks} onInspect={task => { setSelectedTaskId(task.id); setSelectedNode(task.nodes[0]?.id ?? ""); navigate("graph"); }} /></section>
        </>}

        {page === "vision" && <div className="detail-grid"><section className="panel"><SectionTitle title="World observation" subtitle={hybrid ? "Latest task observation; refreshed after visual actions" : "Synthetic scene rendered from the simulation state"} action={<span className="small-pill">{hybrid ? "DESKTOP" : "SIMULATION"}</span>} /><ComputerView snapshot={snapshot} large /><div className="vision-footer"><span>World version <b>{snapshot.worldVersion}</b></span><span>Desktop capture <b>{snapshot.native?.capture ? "Observed" : "Awaiting observation"}</b></span></div></section><section className="panel"><SectionTitle title="Observed facts" subtitle="Current structured world state" /><div className="fact-list">{snapshot.facts.length ? snapshot.facts.map(f => <div className="fact" key={f.key}><span>{f.key}</span><code>{f.value}</code></div>) : <Empty icon="vision" title="No observations yet" text="Run the workspace simulation to populate the world model." />}</div><div className="inline-note"><Icon name="info" size={16} /><p>{hybrid ? "Screen images are captured during visual tasks. The displayed frame may be older than the current desktop; its capture time appears below it." : "This view displays simulation facts."}</p></div></section></div>}

        {page === "graph" && <><section className="panel"><SectionTitle title={latest?.title ?? "Execution graph"} subtitle="Select a node to inspect its dependencies and action." action={latest ? <Status value={latest.status} /> : <span className="small-pill">NO MISSION</span>} />{latest ? <div className="task-graph">{latest.nodes.map((node, i) => <div className="graph-group" key={node.id}>{i > 0 && <div className="graph-connector"><span /><Icon name="chevron" size={12} /></div>}<button className={`graph-node ${selectedNode === node.id ? "selected" : ""}`} onClick={() => setSelectedNode(node.id)}><div><span className="mono">{String(i + 1).padStart(2, "0")}</span><Icon name={isFinished(node.status) ? "check" : "graph"} size={16} /></div><strong>{node.title}</strong><small>{node.action}</small><Status value={node.status} /></button></div>)}</div> : <Empty icon="graph" title="Your next mission starts here" text="Run the fixed simulation from Mission control to create an executable DAG." action={<button className="button secondary" onClick={() => navigate("mission")}>Open mission control<Icon name="arrow" size={14} /></button>} />}</section>{latest && <div className="detail-grid below"><section className="panel"><SectionTitle title="Node inspector" /><pre className="code-block">{JSON.stringify(latest.nodes.find(n => n.id === selectedNode) ?? latest.nodes[0], null, 2)}</pre></section><section className="panel"><SectionTitle title="Execution outcome" /><div className="outcome"><Icon name={isFinished(latest.status) ? "check" : "info"} size={28} /><h3>{formatStatus(latest.status)}</h3><p>{latest.summary || "Execution result appears after the mission finishes."}</p><span className="mono">{latest.id}</span></div></section></div>}</>}

        {page === "timeline" && <section className="panel"><SectionTitle title="Event ledger" subtitle="Ordered session events. Export preserves the current retained window." action={<button className="button secondary" onClick={exportEvents} disabled={!snapshot.events.length}><Icon name="download" size={14} />Export JSON</button>} /><div className="filter-bar"><Icon name="search" size={16} /><input aria-label="Filter events" placeholder="Filter by event type, mission, or summary…" value={search} onChange={e => setSearch(e.target.value)} /><span>{snapshot.events.filter(e => `${e.type} ${e.taskId} ${e.summary}`.toLowerCase().includes(search.toLowerCase())).length} events</span></div><EventList events={snapshot.events.filter(e => `${e.type} ${e.taskId} ${e.summary}`.toLowerCase().includes(search.toLowerCase())).slice().reverse()} /></section>}

        {page === "memory" && <section className="panel"><SectionTitle title="Evidence becomes memory" subtitle="Session memories are derived from verified runs. Current observations take precedence." action={<span className="small-pill">IN MEMORY</span>} /><div className="filter-bar"><Icon name="search" size={16} /><input aria-label="Search memories" placeholder="Search memory content or type…" value={search} onChange={e => setSearch(e.target.value)} /></div>{snapshot.memory.filter(m => `${m.kind} ${m.content}`.toLowerCase().includes(search.toLowerCase())).length ? <div className="memory-list">{snapshot.memory.filter(m => `${m.kind} ${m.content}`.toLowerCase().includes(search.toLowerCase())).map(m => <article className="memory-item" key={m.id}><div className="memory-icon"><Icon name="memory" /></div><div><span className="overline">{m.kind}</span><h3>{m.content}</h3><p>Recorded {time(m.createdAt)} · {m.confidence === null ? "Recorded outcome" : `confidence ${Math.round(m.confidence * 100)}%`}</p></div><Icon name="check" size={16} /></article>)}</div> : <Empty icon="memory" title={search ? "No matching memories" : "A fresh memory space"} text={search ? "Try another phrase or clear your search." : "Complete a simulation to record its verified outcome here."} />}</section>}

        {page === "models" && <><div className="banner notice"><Icon name="info" /><span>{hybrid ? "Full Access uses your configured model provider. Text and screen observations may be sent to that provider during missions." : "The demo uses a deterministic planner. No provider requests are involved."}</span></div><section className="panel"><SectionTitle title="Routing architecture" subtitle="Provider tiers defined by capability, privacy, cost, and available resources." /><div className="model-tiers">{[{ tier: "A", title: "Local fast", detail: "OCR · embeddings · classification", budget: "Small footprint", icon: "resources" }, { tier: "B", title: "Local advanced", detail: "Vision · moderate reasoning · offline", budget: "One resident model", icon: "models" }, { tier: "C", title: "Cloud frontier", detail: "Long-horizon planning · complex recovery", budget: "Explicit egress policy", icon: "graph" }].map(m => <article className="model-tier" key={m.tier}><div className="tier-head"><span className="tier-letter">{m.tier}</span><span className="small-pill">ARCHITECTURE</span></div><Icon name={m.icon} size={27} /><h3>{m.title}</h3><p>{m.detail}</p><div className="tier-footer"><span>{m.budget}</span><span>Not connected</span></div></article>)}</div><div className="inline-note"><Icon name="security" /><p>Restricted data stays local. Provider unavailability never changes permission or verification requirements. Model consensus is supporting evidence; it is not proof of an action outcome.</p></div></section></>}

        {page === "security" && <SecurityView snapshot={snapshot} />}
        {page === "resources" && <ResourcesView snapshot={snapshot} />}
        {page === "recovery" && <><section className="panel"><SectionTitle title="Recovery state" subtitle="Failure freezes execution. Recovery always passes through policy again." action={<Status value={halted ? "EMERGENCY_STOP" : "MONITORING"} />} /><div className="recovery-path">{["Detect", "Freeze", "Classify", "Collect evidence", "Re-plan", "Authorize", "Verify"].map((s, i) => <div key={s}><span>{String(i + 1).padStart(2, "0")}</span><strong>{s}</strong>{i < 6 && <Icon name="arrow" size={14} />}</div>)}</div><EventList events={snapshot.events.filter(e => /FAIL|RECOVER|ERROR|STOP|DENIED/.test(e.type)).slice().reverse()} /></section><div className="inline-note"><Icon name="info" /><p>Ambiguous execution outcomes require inspection before retry. A desktop side effect cannot be made exactly-once by a database transaction. {hybrid ? "Use Emergency stop or Ctrl+Alt+Escape to stop the desktop worker. Inspect uncertain actions before continuing from a checkpoint." : "This reference UI stop controls the simulation."}</p></div></>}

        <footer className="page-footer"><span><span className="footer-mark">✳</span> JARVIS X <span className="muted">/</span> Verified autonomous operating fabric</span><span>REFERENCE IMPLEMENTATION <span className="footer-dot">·</span> v0.1.0</span></footer>
      </main>
    </div>

    {palette && <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) { setPalette(false); commandButtonRef.current?.focus(); } }}><div className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette" ref={paletteDialogRef} onKeyDown={e => { if (e.key === "Tab") { const els = paletteDialogRef.current?.querySelectorAll<HTMLElement>("input, button:not(:disabled)"); const first = els?.[0]; const last = els?.[els.length - 1]; if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); } } }}><div className="palette-input"><Icon name="search" size={20} /><input ref={paletteRef} value={paletteQuery} onChange={e => setPaletteQuery(e.target.value)} aria-label="Search commands" placeholder="Where would you like to go?" /><button aria-label="Close command palette" onClick={() => { setPalette(false); commandButtonRef.current?.focus(); }}><kbd>Esc</kbd></button></div><p className="palette-label">NAVIGATE</p><div className="palette-options">{navigation.filter(n => n.name.toLowerCase().includes(paletteQuery.toLowerCase())).map(n => <button key={n.id} onClick={() => navigate(n.id)}><Icon name={n.id} /><span>{n.name}</span><Icon name="arrow" size={15} /></button>)}{!navigation.some(n => n.name.toLowerCase().includes(paletteQuery.toLowerCase())) && <p className="palette-empty">No matching pages.</p>}</div><p className="palette-label">CONTROLS</p><div className="palette-options"><button disabled={busy || connection !== "connected" || halted} onClick={() => { setPalette(false); void control("run"); }}><Icon name="play" /><span>{hybrid ? "Run desktop mission" : "Run workspace simulation"}</span></button><button disabled={busy || connection !== "connected" || halted} onClick={() => { setPalette(false); void control("stop"); }}><Icon name="stop" /><span>{hybrid ? "Emergency stop" : "Stop simulation"}</span></button></div><div className="palette-footer"><span>Use Tab to navigate · Enter to select</span><span>JARVIS X</span></div></div></div>}
  </div>;
}

const subtitles: Record<Exclude<PageId, "mission">, string> = {
  vision: "Current observations, their sources, and the world they describe.",
  graph: "A mission decomposed into bounded, verifiable steps.",
  timeline: "Follow the evidence from intent to committed state.",
  memory: "Verified experience, with provenance and freshness.",
  models: "The right capability, within the right boundary.",
  security: "Explicit authority. Narrow scope. Inspectable decisions.",
  resources: "A bounded workload, with room for the rest of your machine.",
  recovery: "Detect the unexpected. Restore a known state."
};

function Metric({ label, value, detail, icon }: { label: string; value: string; detail: string; icon: string }) {
  return <div className="metric"><div className="metric-label">{label}<Icon name={icon} size={15} /></div><strong>{value}</strong><small>{detail}</small></div>;
}

function Empty({ icon, title, text, action }: { icon: string; title: string; text: string; action?: React.ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Icon name={icon} size={24} /></div><h3>{title}</h3><p>{text}</p>{action}</div>;
}

function ComputerView({ snapshot, large = false }: { snapshot: Snapshot; large?: boolean }) {
  if (snapshot.mode === "hybrid") {
    const capture = snapshot.native?.capture;
    return capture ? <div className="computer-view" style={{ padding: 12 }}>
      <img src={capture.previewDataUrl} alt="Latest JARVIS desktop observation" style={{ display: "block", width: "100%", maxHeight: large ? 650 : 340, objectFit: "contain" }} />
      <p className="mono" style={{ fontSize: 11 }}>Captured {new Date(capture.capturedAtMs).toLocaleTimeString()} · {capture.display.width} × {capture.display.height} · Input uses a fresh observation</p>
    </div> : <Empty icon="vision" title="Awaiting a screen observation" text="Visual missions publish their latest desktop capture here. Semantic browser and file tasks can run without taking a screenshot." />;
  }
  const documents = snapshot.facts.filter(f => f.key.startsWith("file:") && f.key.endsWith(":exists") && f.value === "true").map(f => ({ key: f.key.slice(5, -7).split("/").pop() ?? f.key, value: f.value }));
  return <div className={`computer-view ${large ? "large" : ""}`}><div className="computer-toolbar"><div className="window-dots"><i /><i /><i /></div><span>JARVIS WORKSPACE</span><span className="synthetic-label">SIMULATED</span></div><div className="synthetic-desktop"><div className="file-window"><div className="file-window-sidebar"><Icon name="layers" size={20} /><span className="file-sidebar-selected"><Icon name="file" size={14} />Workspace</span><span><Icon name="memory" size={14} />Evidence</span><span><Icon name="recovery" size={14} />History</span><div className="file-sidebar-bottom"><i /> Local sandbox</div></div><div className="file-window-content"><div className="file-breadcrumb">workspace <Icon name="chevron" size={11} /> reports</div><div className="file-heading"><div><h3>Workspace report</h3><p>{snapshot.tasks.length ? "Derived from the active simulation" : "Ready for a verified run"}</p></div><Icon name="file" size={20} /></div><div className="document-table"><div className="document-row document-header"><span>ARTIFACT</span><span>STATE</span></div>{documents.length ? documents.slice(0, 3).map(f => <div className="document-row" key={f.key}><span><Icon name="file" size={13} />{f.key.length > 30 ? `${f.key.slice(0, 27)}…` : f.key}</span><span className="document-status">Observed</span></div>) : <><div className="document-placeholder"><Icon name="file" size={26} /><span>Report will appear after execution</span></div></>}</div><div className="document-provenance"><Icon name="security" size={13} /><span>{snapshot.worldVersion > 0 ? `World state v${snapshot.worldVersion} · simulation evidence` : "Awaiting first observation"}</span></div></div></div><span className="view-corner tl" /><span className="view-corner tr" /><span className="view-corner bl" /><span className="view-corner br" /></div></div>;
}

function EventList({ events, compact = false }: { events: EventView[]; compact?: boolean }) {
  if (!events.length) return <Empty icon="timeline" title="Quiet, and ready" text="Events appear here when the simulation starts. Every transition will leave a trace." />;
  return <div className={`event-list ${compact ? "compact" : ""}`}>{events.map(e => <div className="event-item" key={e.id}><div className={`event-icon ${/FAIL|STOP|ERROR/.test(e.type) ? "error" : ""}`}><Icon name={/VERIF|COMPLET/.test(e.type) ? "check" : /POLICY|CAPABILITY|APPROV/.test(e.type) ? "security" : /PLAN/.test(e.type) ? "graph" : /FAIL|STOP|ERROR/.test(e.type) ? "stop" : "resources"} size={14} /></div><div className="event-main"><strong>{formatStatus(e.type)}</strong><p>{e.summary || e.taskId || "Runtime state transition"}</p>{!compact && <span className="mono">#{e.sequence} · {e.taskId || "system"}</span>}</div><time dateTime={e.timestamp}>{time(e.timestamp)}</time></div>)}</div>;
}

function MissionTable({ tasks, onInspect }: { tasks: TaskView[]; onInspect: (task: TaskView) => void }) {
  if (!tasks.length) return <div className="mission-table-empty"><div><Icon name="graph" size={20} /><span>No missions yet. Start with the workspace-report simulation.</span></div><span className="mono">AWAITING INTENT</span></div>;
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>MISSION</th><th>STATUS</th><th>PLAN NODES</th><th>CREATED</th><th><span className="sr-only">Inspect</span></th></tr></thead><tbody>{tasks.map(t => <tr key={t.id}><td><div className="mission-name"><Icon name="graph" size={17} /><div><strong>{t.title}</strong><small className="mono">{t.id.slice(0, 20)}</small></div></div></td><td><Status value={t.status} /></td><td className="mono">{t.nodes.length.toString().padStart(2, "0")}</td><td className="mono">{time(t.createdAt)}</td><td><button className="icon-button" aria-label={`Inspect ${t.title}`} onClick={() => onInspect(t)}><Icon name="arrow" size={16} /></button></td></tr>)}</tbody></table></div>;
}

function SecurityView({ snapshot }: { snapshot: Snapshot }) {
  if (snapshot.mode === "hybrid") return <section className="panel">
    <SectionTitle title="Desktop access boundaries" subtitle="Permissions belong to this local session and are revoked by emergency stop." />
    <div className="policy-rows">{[
      ["Full Access", "Enabled only by the operator. Standard mode supports bounded application launches."],
      ["Terminal commands", "Require the additional terminal checkbox when enabling Full Access. Commands run with your Windows account permissions; this is not an OS sandbox."],
      ["Screen observations", "Raw input requires a recent stable foreground observation. Screen images may be sent to your configured model provider."],
      ["Action verification", "Mutations require observed review before another mutation. Uncertain actions are saved for inspection and are never automatically replayed."],
      ["Emergency stop", "The button and Ctrl+Alt+Escape cancel the worker, release tracked inputs, and request command-tree termination. Reset leaves Full Access disabled."],
    ].map(([title, detail]) => <div className="policy-row" key={title}><Icon name="security" /><div><h3>{title}</h3><p>{detail}</p></div></div>)}</div>
  </section>;
  return <><div className="security-summary"><Icon name="security" size={32} /><div><span className="overline">DEFAULT DENY</span><h2>Authority is explicit.</h2><p>The simulation owns its grants. Mission input cannot create permissions.</p></div><span className="small-pill">SIMULATION POLICY</span></div><section className="panel"><SectionTitle title="Execution boundaries" subtitle="Runtime guarantees and their integration limits" /><div className="policy-rows">{[{ title: "Typed actions only", text: "Structured, validated requests. No raw model-to-shell execution.", status: "ENFORCED" }, { title: "Capability scope & expiry", text: "Actions require a server-owned grant that matches their scope.", status: "ENFORCED" }, { title: "Observation freshness", text: "Stale or missing evidence prevents state-dependent execution.", status: "ENFORCED" }, { title: "Verification before commit", text: "An observed result must satisfy the expected outcome.", status: "ENFORCED" }, { title: "Native OS sandbox", text: "Windows/Linux isolation requires deployment-specific integration.", status: "NOT CONNECTED" }, { title: "Physical emergency path", text: "The dashboard stop cancels simulation work. Hardware stop is a separate native requirement.", status: "NOT CONNECTED" }].map(p => <div className="policy-row" key={p.title}><Icon name={p.status === "ENFORCED" ? "security" : "info"} size={18} /><div><h3>{p.title}</h3><p>{p.text}</p></div><span className={`small-pill ${p.status === "ENFORCED" ? "green" : ""}`}>{p.status}</span></div>)}</div></section><section className="panel below"><SectionTitle title="Active capability grants" subtitle="Granted by the reference runtime, scoped to the in-memory environment" />{snapshot.capabilities.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>PERMISSION</th><th>SCOPE</th><th>EXPIRES</th></tr></thead><tbody>{snapshot.capabilities.map(c => <tr key={c.id}><td className="mono">{c.permission}</td><td className="mono">{c.scope}</td><td>{time(c.expiresAt)}</td></tr>)}</tbody></table></div> : <Empty icon="security" title="No active grants to display" text="Capabilities are issued and checked by the runtime for each mission." />}</section></>;
}

function ResourcesView({ snapshot }: { snapshot: Snapshot }) {
  const telemetry = snapshot.telemetry;
  const ratio = telemetry.hostRamTotalGb ? telemetry.hostRamUsedGb / telemetry.hostRamTotalGb : 0;
  return <><div className="metrics-strip"><Metric label="CONTROL CENTER RSS" value={telemetry.processRssMb ? `${telemetry.processRssMb} MB` : "—"} detail="Measured Node.js process memory" icon="resources" /><Metric label="HOST MEMORY" value={telemetry.hostRamTotalGb ? `${telemetry.hostRamUsedGb.toFixed(1)} GB` : "—"} detail={`of ${telemetry.hostRamTotalGb.toFixed(1)} GB physical RAM`} icon="memory" /><Metric label="GPU / VRAM" value="Unavailable" detail="Native GPU telemetry not connected" icon="models" /><Metric label="CLOUD INFERENCE" value="0 requests" detail="Deterministic simulation only" icon="graph" /></div><div className="detail-grid"><section className="panel"><SectionTitle title="Memory headroom" subtitle="Measured host memory; includes other applications" /><div className="resource-gauge"><div><strong>{Math.round(ratio * 100)}<span>%</span></strong><span>HOST RAM IN USE</span></div><div className="gauge-track"><span style={{ width: `${Math.min(ratio * 100, 100)}%` }} /></div><div className="gauge-labels"><span>0 GB</span><span>{telemetry.hostRamTotalGb.toFixed(1)} GB</span></div><p>Sampled {time(telemetry.sampledAt)}. Host memory pressure does not represent JARVIS allocation alone.</p></div></section><section className="panel"><SectionTitle title="Deployment budget" subtitle="Architecture targets · 16 GB RAM / 8 GB VRAM" /><div className="budget-list">{[{ label: "OS, desktop & other applications", value: "4 GiB RAM" }, { label: "Models, context & workspace", value: "4.75 GiB RAM" }, { label: "Services, database & perception", value: "3.75 GiB RAM" }, { label: "Reserved memory headroom", value: "3.5 GiB RAM" }, { label: "GPU model, context & activations", value: "5.25 GiB VRAM" }, { label: "GPU headroom, capture & compositor", value: "2.75 GiB VRAM" }].map(r => <div key={r.label}><span>{r.label}</span><strong className="mono">{r.value}</strong></div>)}</div></section></div><div className="inline-note"><Icon name="info" /><p>Budgets are design allocations, not measured performance promises. Native CPU, GPU, thermal, and capture metrics remain unavailable until their adapters are connected.</p></div></>;
}
