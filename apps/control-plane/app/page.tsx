"use client";

import { FormEvent, MouseEvent, useCallback, useEffect, useRef, useState } from "react";

type Intervention = {
  intervention_id: string;
  run_id: string;
  session_id: string;
  status: string;
  control_owner: string;
  lease_version: number;
  lease_expires_at: string;
};

type ErrorBody = { code?: string; message?: string };
type HumanKey = "Enter" | "Escape" | "Tab" | "Shift+Tab" | "Backspace" | "Delete" | "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";
type HumanInput =
  | { kind: "pointer"; x: number; y: number }
  | { kind: "text"; text: string }
  | { kind: "key"; key: HumanKey };
type ViewportFrame = {
  sequence: number;
  width: number;
  height: number;
  nextClientSequence: number;
};

async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & ErrorBody;
  if (!response.ok) {
    throw new Error(body.message ?? body.code ?? "Runtime request failed.");
  }
  return body;
}

export default function InterventionConsole() {
  const [interventionId, setInterventionId] = useState("");
  const [operatorId, setOperatorId] = useState("operator-7");
  const [intervention, setIntervention] = useState<Intervention | null>(null);
  const [viewportUrl, setViewportUrl] = useState<string | null>(null);
  const [viewportFrame, setViewportFrame] = useState<ViewportFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [manualText, setManualText] = useState("");
  const [manualKey, setManualKey] = useState<HumanKey>("Enter");
  const mutationInFlight = useRef(false);

  const requestTransition = useCallback(
    async (transition: "claim" | "release" | "resume" | "heartbeat" | "terminate") => {
      if (!intervention || mutationInFlight.current) return;
      mutationInFlight.current = true;
      setPending(true);
      setError(null);
      try {
        const payload = {
          expected_lease_version: intervention.lease_version,
          operator_id: operatorId,
          ...(transition === "terminate" ? { resolution: "Terminated by operator." } : {}),
        };
        const response = await fetch(
          `/runtime/api/v1/interventions/${encodeURIComponent(intervention.intervention_id)}/${transition}`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(payload),
          },
        );
        setIntervention(await readJson<Intervention>(response));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Runtime request failed.");
      } finally {
        mutationInFlight.current = false;
        setPending(false);
      }
    },
    [intervention, operatorId],
  );

  useEffect(() => {
    if (!intervention || intervention.control_owner !== `human:${operatorId}`) return;
    const controller = new AbortController();
    const loadFrame = async () => {
      try {
        const query = new URLSearchParams({
          expected_lease_version: String(intervention.lease_version),
          operator_id: operatorId,
        });
        const response = await fetch(
          `/runtime/api/v1/interventions/${encodeURIComponent(intervention.intervention_id)}/viewport?${query}`,
          { cache: "no-store", signal: controller.signal },
        );
        if (!response.ok) throw new Error("Live viewport is unavailable for this lease.");
        const sequence = Number(response.headers.get("x-replayforge-frame-sequence"));
        const width = Number(response.headers.get("x-replayforge-viewport-width"));
        const height = Number(response.headers.get("x-replayforge-viewport-height"));
        const nextClientSequence = Number(response.headers.get("x-replayforge-next-client-sequence"));
        if (
          ![sequence, width, height, nextClientSequence].every(
            (value) => Number.isSafeInteger(value) && value > 0,
          )
        ) {
          throw new Error("Live viewport metadata is invalid.");
        }
        const nextUrl = URL.createObjectURL(await response.blob());
        setViewportUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return nextUrl;
        });
        setViewportFrame({ sequence, width, height, nextClientSequence });
      } catch (cause) {
        if (!controller.signal.aborted) {
          setViewportFrame(null);
          setViewportUrl((previous) => {
            if (previous) URL.revokeObjectURL(previous);
            return null;
          });
          setError(cause instanceof Error ? cause.message : "Viewport request failed.");
        }
      }
    };
    void loadFrame();
    const interval = window.setInterval(() => void loadFrame(), 2_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [intervention, operatorId]);

  useEffect(() => {
    setViewportFrame(null);
    setViewportUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return null;
    });
  }, [intervention?.lease_version]);

  useEffect(() => {
    if (!intervention || intervention.control_owner !== `human:${operatorId}`) return;
    const interval = window.setInterval(() => void requestTransition("heartbeat"), 10_000);
    return () => window.clearInterval(interval);
  }, [intervention, operatorId, requestTransition]);

  useEffect(
    () => () => {
      if (viewportUrl) URL.revokeObjectURL(viewportUrl);
    },
    [viewportUrl],
  );

  const sendInput = useCallback(
    async (input: HumanInput) => {
      if (!intervention || !viewportFrame || intervention.control_owner !== `human:${operatorId}` || mutationInFlight.current) return false;
      mutationInFlight.current = true;
      setPending(true);
      setError(null);
      try {
        const response = await fetch(
          `/runtime/api/v1/interventions/${encodeURIComponent(intervention.intervention_id)}/input`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              expected_lease_version: intervention.lease_version,
              operator_id: operatorId,
              client_sequence: viewportFrame.nextClientSequence,
              source_frame_sequence: viewportFrame.sequence,
              viewport_width: viewportFrame.width,
              viewport_height: viewportFrame.height,
              input,
            }),
          },
        );
        await readJson(response);
        setViewportFrame(null);
        setViewportUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return null;
        });
        return true;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Human input was rejected.");
        return false;
      } finally {
        mutationInFlight.current = false;
        setPending(false);
      }
    },
    [intervention, operatorId, viewportFrame],
  );

  function clickViewport(event: MouseEvent<HTMLImageElement>) {
    if (!viewportFrame || pending) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = Math.min(
      viewportFrame.width - 1,
      Math.max(0, Math.floor(((event.clientX - bounds.left) / bounds.width) * viewportFrame.width)),
    );
    const y = Math.min(
      viewportFrame.height - 1,
      Math.max(0, Math.floor(((event.clientY - bounds.top) / bounds.height) * viewportFrame.height)),
    );
    void sendInput({ kind: "pointer", x, y });
  }

  async function submitText(event: FormEvent) {
    event.preventDefault();
    if (!manualText) return;
    if (await sendInput({ kind: "text", text: manualText })) setManualText("");
  }

  async function load(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const response = await fetch(
        `/runtime/api/v1/interventions/${encodeURIComponent(interventionId)}`,
        { cache: "no-store" },
      );
      setIntervention(await readJson<Intervention>(response));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Intervention lookup failed.");
    } finally {
      setPending(false);
    }
  }

  const owned = intervention?.control_owner === `human:${operatorId}`;
  return (
    <main>
      <header>
        <div className="mark">R</div>
        <div>
          <p className="eyebrow">ReplayForge</p>
          <h1>Intervention console</h1>
        </div>
        <span className="environment">Local · synthetic data</span>
      </header>

      <section className="lookup" aria-labelledby="lookup-heading">
        <div>
          <p className="eyebrow">Same-session handoff</p>
          <h2 id="lookup-heading">Open an intervention</h2>
          <p>Claims are exclusive. Every viewport request and transition uses the current lease version.</p>
        </div>
        <form onSubmit={load}>
          <label>
            Intervention ID
            <input value={interventionId} onChange={(event) => setInterventionId(event.target.value)} required />
          </label>
          <label>
            Operator ID
            <input value={operatorId} onChange={(event) => setOperatorId(event.target.value)} required />
          </label>
          <button disabled={pending}>Load intervention</button>
        </form>
      </section>

      {error ? <div className="error" role="alert">{error}</div> : null}
      {intervention ? (
        <section className="workspace" aria-live="polite">
          <div className="viewport-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Live retained browser</p>
                <h2>Current viewport</h2>
              </div>
              <span className={`status status-${intervention.status}`}>{intervention.status}</span>
            </div>
            <div className="viewport">
              {owned && viewportUrl ? (
                // The source is a short-lived same-origin blob generated from a no-store response.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={viewportUrl}
                  alt="Current retained browser viewport; click to send a left-click"
                  onClick={clickViewport}
                />
              ) : (
                <p>{owned ? "Waiting for the first frame…" : "Claim control to view the live session."}</p>
              )}
            </div>
          </div>

          <aside>
            <p className="eyebrow">Control lease</p>
            <dl>
              <div><dt>Owner</dt><dd>{intervention.control_owner}</dd></div>
              <div><dt>Version</dt><dd>{intervention.lease_version}</dd></div>
              <div><dt>Frame</dt><dd>{viewportFrame ? `${viewportFrame.sequence} · ${viewportFrame.width}×${viewportFrame.height}` : "—"}</dd></div>
              <div><dt>Expires</dt><dd>{new Date(intervention.lease_expires_at).toLocaleTimeString()}</dd></div>
              <div><dt>Run</dt><dd>{intervention.run_id}</dd></div>
              <div><dt>Session</dt><dd>{intervention.session_id}</dd></div>
            </dl>
            <div className="actions">
              <button disabled={pending || intervention.status !== "open"} onClick={() => void requestTransition("claim")}>Claim control</button>
              <button disabled={pending || !owned} onClick={() => void requestTransition("heartbeat")}>Renew lease</button>
              <button className="secondary" disabled={pending || !owned} onClick={() => void requestTransition("release")}>Release</button>
              <button className="secondary" disabled={pending || !owned} onClick={() => void requestTransition("resume")}>Begin resume</button>
              <button className="danger" disabled={pending || (!owned && intervention.status !== "open")} onClick={() => void requestTransition("terminate")}>Terminate</button>
            </div>
            <div className="manual-input" aria-label="Manual session input">
              <p className="eyebrow">Manual input</p>
              <p>Click the current frame, or send text to the control already focused in the retained session.</p>
              <form onSubmit={submitText}>
                <label>
                  Text (not retained)
                  <input
                    value={manualText}
                    onChange={(event) => setManualText(event.target.value)}
                    maxLength={1000}
                    autoComplete="off"
                    disabled={!owned || !viewportFrame || pending}
                  />
                </label>
                <button disabled={!owned || !viewportFrame || pending || !manualText}>Send text</button>
              </form>
              <div className="key-input">
                <label>
                  Navigation key
                  <select value={manualKey} onChange={(event) => setManualKey(event.target.value as HumanKey)} disabled={!owned || !viewportFrame || pending}>
                    {['Enter', 'Escape', 'Tab', 'Shift+Tab', 'Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].map((key) => <option key={key}>{key}</option>)}
                  </select>
                </label>
                <button disabled={!owned || !viewportFrame || pending} onClick={() => void sendInput({ kind: "key", key: manualKey })}>Send key</button>
              </div>
            </div>
            <p className="boundary">Input is accepted once against the latest frame and current lease. Typed text is never written to the audit log.</p>
          </aside>
        </section>
      ) : null}
    </main>
  );
}
