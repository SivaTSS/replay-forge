"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

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
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const requestTransition = useCallback(
    async (transition: "claim" | "release" | "resume" | "heartbeat" | "terminate") => {
      if (!intervention) return;
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
        const nextUrl = URL.createObjectURL(await response.blob());
        setViewportUrl((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return nextUrl;
        });
      } catch (cause) {
        if (!controller.signal.aborted) {
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
                <img src={viewportUrl} alt="Current retained browser viewport" />
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
            <p className="boundary">Manual pointer and keyboard forwarding are not enabled in this build.</p>
          </aside>
        </section>
      ) : null}
    </main>
  );
}
