"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

export function ControlSessionGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const requestGeneration = useRef(0);
  const pairing = useRef(false);
  const refresh = useCallback(async () => {
    const generation = ++requestGeneration.current;
    try {
      const response = await fetch("/api/session", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error("Session service is unavailable");
      const result = await response.json();
      if (typeof result.authenticated !== "boolean" || typeof result.pairingAvailable !== "boolean") throw new Error("Session service returned an invalid response");
      if (generation !== requestGeneration.current) return false;
      setAuthenticated(result.authenticated);
      setAvailable(result.pairingAvailable);
      return result.authenticated as boolean;
    } catch {
      // A transient status outage must not hide an already connected user's
      // pause/stop controls. Mutations still enforce the signed session server-side.
      if (generation === requestGeneration.current) setAuthenticated(previous => previous === true);
      return false;
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!pairing.current) await refresh();
      if (!stopped) timer = setTimeout(poll, 2500);
    };
    void poll();
    return () => { stopped = true; requestGeneration.current++; clearTimeout(timer); };
  }, [refresh]);

  const pair = async (file?: File) => {
    if (!file || pairing.current) return;
    pairing.current = true;
    requestGeneration.current++;
    setBusy(true); setError("");
    try {
      if (file.size > 2048) throw new Error("Choose the small control-session.json file created by JARVIS.");
      const value = JSON.parse(await file.text());
      if (value.version !== 1 || typeof value.token !== "string" || value.token.length < 32 || value.token.length > 256 ||
          typeof value.url !== "string") throw new Error("Choose the control-session.json file created by JARVIS.");
      if (new URL(value.url).origin !== window.location.origin) throw new Error("Open the dashboard at http://127.0.0.1:3000 and connect there.");
      const response = await fetch("/api/pair", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Jarvis-Control": "pair" },
        body: JSON.stringify({ token: value.token }), signal: AbortSignal.timeout(6000) });
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error(result.error || "Browser pairing failed");
      // Verify the browser retained its HttpOnly cookie; a 200 response alone
      // must not hide blocked cookies or render unusable action controls.
      if (!await refresh()) throw new Error("The browser did not retain its session cookie. Allow cookies for this local dashboard and reconnect.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not connect this browser"); }
    finally { pairing.current = false; setBusy(false); if (fileInput.current) fileInput.current.value = ""; }
  };

  if (authenticated) return <>{children}</>;
  return <main className="console session-connect" aria-label="Connect JARVIS browser session">
    <section>
      <div className="eyebrow">JARVIS X / LOCAL SESSION</div>
      <h1>{authenticated === null ? "Checking your session…" : "Connect this browser"}</h1>
      <p>The runtime is local, but each browser needs its own authenticated connection. A runtime restart also requires reconnection.</p>
      <p>Choose <code>control-session.json</code> from the project’s <code>.jarvis</code> folder. JARVIS refreshes it at startup.</p>
      {!available && authenticated !== null && <p>Start JARVIS with <code>npm run dev</code> to create the current pairing file.</p>}
      <input ref={fileInput} type="file" accept=".json,application/json" aria-label="Runtime pairing file" hidden
        onChange={event => void pair(event.target.files?.[0])} />
      <button className="button primary" disabled={busy || !available} onClick={() => fileInput.current?.click()}>
        {busy ? "Connecting…" : "Choose pairing file"}
      </button>
      <button className="button secondary" disabled={busy} onClick={() => void refresh()}>Check connection</button>
      {error && <p role="alert">{error}</p>}
      <p className="session-note">Pairing does not enable Full Access or terminal permissions.</p>
    </section>
  </main>;
}
