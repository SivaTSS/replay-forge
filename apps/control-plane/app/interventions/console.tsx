"use client";

import {
  FormEvent,
  MouseEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

type Intervention = {
  intervention_id: string;
  run_id: string;
  session_id: string;
  status: "open" | "claimed" | "resuming" | "resolved" | "terminated";
  control_owner: string;
  lease_version: number;
  lease_expires_at: string;
  run_mode: "discovery" | "replay" | null;
  application_family: string | null;
  tenant: string | null;
  task_summary: string | null;
  capability_id: string | null;
  capability_version: string | null;
  capability_name: string | null;
  step_id: string | null;
  trigger_code: string;
  explanation: string;
  surface_route: string | null;
  created_at: string;
};
type RunResult = {
  status: "success" | "business_outcome" | "failure" | "intervention_required";
  code?: string;
  intervention_id?: string;
};
type TransitionResponse = Intervention & { result?: RunResult | null };
type ErrorBody = { code?: string; message?: string; correlation_id?: string };
type HumanKey =
  | "Enter"
  | "Escape"
  | "Tab"
  | "Shift+Tab"
  | "Backspace"
  | "Delete"
  | "ArrowUp"
  | "ArrowDown"
  | "ArrowLeft"
  | "ArrowRight";
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

class ApiError extends Error {
  constructor(
    readonly body: ErrorBody,
    readonly status: number,
  ) {
    super(body.message ?? body.code ?? "Runtime request failed.");
  }
}
async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & ErrorBody;
  if (!response.ok) throw new ApiError(body, response.status);
  return body;
}
function errorMessage(cause: unknown): string {
  if (!(cause instanceof ApiError))
    return cause instanceof Error ? cause.message : "Runtime request failed.";
  return `${cause.message}${cause.body.correlation_id ? ` Reference ${cause.body.correlation_id}.` : ""}`;
}
function expired(item: Intervention, now: number): boolean {
  return item.status === "claimed" && Date.parse(item.lease_expires_at) <= now;
}

export default function InterventionConsole({
  initialInterventionId,
  embedded = false,
}: {
  initialInterventionId?: string;
  embedded?: boolean;
}) {
  const [inbox, setInbox] = useState<Intervention[]>([]);
  const [interventionId, setInterventionId] = useState("");
  const [operatorId, setOperatorId] = useState("operator-7");
  const [intervention, setIntervention] = useState<Intervention | null>(null);
  const [viewportUrl, setViewportUrl] = useState<string | null>(null);
  const [frame, setFrame] = useState<ViewportFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [manualText, setManualText] = useState("");
  const [manualKey, setManualKey] = useState<HumanKey>("Enter");
  const [resumeResult, setResumeResult] = useState<RunResult | null>(null);
  const [confirmTerminate, setConfirmTerminate] = useState(false);
  const [now, setNow] = useState(Date.now());
  const mutationInFlight = useRef(false);
  const frameInFlight = useRef(false);
  const selectedIntervention = useRef<string | null>(null);
  const loadSequence = useRef(0);

  const refreshInbox = useCallback(async (quiet = false) => {
    try {
      const response = await fetch(
        "/runtime/api/v1/interventions?run_mode=replay",
        { cache: "no-store" },
      );
      setInbox((await readJson<{ items: Intervention[] }>(response)).items);
      if (!quiet) setError(null);
    } catch (cause) {
      if (!quiet) setError(errorMessage(cause));
    }
  }, []);

  const loadById = useCallback(async (id: string, quiet = false) => {
    if (!quiet) {
      if (selectedIntervention.current !== id) setIntervention(null);
      selectedIntervention.current = id;
    }
    if (selectedIntervention.current !== id) return null;
    const sequence = ++loadSequence.current;
    try {
      const response = await fetch(
        `/runtime/api/v1/interventions/${encodeURIComponent(id)}`,
        { cache: "no-store" },
      );
      const next = await readJson<Intervention>(response);
      if (
        selectedIntervention.current !== id ||
        sequence !== loadSequence.current
      )
        return null;
      setIntervention((current) =>
        current?.intervention_id === id &&
        current.lease_version > next.lease_version
          ? current
          : next,
      );
      setInterventionId(next.intervention_id);
      if (!quiet) {
        setError(null);
        setResumeResult(null);
      }
      return next;
    } catch (cause) {
      if (
        !quiet &&
        selectedIntervention.current === id &&
        sequence === loadSequence.current
      )
        setError(errorMessage(cause));
      return null;
    }
  }, []);

  useEffect(() => {
    if (initialInterventionId) void loadById(initialInterventionId);
  }, [initialInterventionId, loadById]);

  useEffect(() => {
    void refreshInbox();
    const timer = window.setInterval(() => void refreshInbox(true), 3_000);
    return () => window.clearInterval(timer);
  }, [refreshInbox]);
  useEffect(() => {
    if (
      !intervention ||
      ["resolved", "terminated"].includes(intervention.status)
    )
      return;
    const timer = window.setInterval(
      () => void loadById(intervention.intervention_id, true),
      3_000,
    );
    return () => window.clearInterval(timer);
  }, [intervention?.intervention_id, intervention?.status, loadById]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const transition = useCallback(
    async (
      action: "claim" | "release" | "resume" | "heartbeat" | "terminate",
      quiet = false,
    ) => {
      if (
        !intervention ||
        mutationInFlight.current ||
        selectedIntervention.current !== intervention.intervention_id
      )
        return;
      mutationInFlight.current = true;
      if (!quiet) {
        setPending(action);
        setError(null);
        setNotice(null);
      }
      try {
        const response = await fetch(
          `/runtime/api/v1/interventions/${encodeURIComponent(intervention.intervention_id)}/${action}`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              expected_lease_version: intervention.lease_version,
              operator_id: operatorId,
              ...(action === "terminate"
                ? { resolution: "Terminated by operator." }
                : {}),
            }),
          },
        );
        const next = await readJson<TransitionResponse>(response);
        if (selectedIntervention.current !== intervention.intervention_id)
          return;
        setResumeResult(next.result ?? null);
        if (
          next.result?.status === "intervention_required" &&
          next.result.intervention_id
        ) {
          await loadById(next.result.intervention_id);
          setNotice(
            "Replay paused again. The new intervention is ready to claim.",
          );
        } else {
          setIntervention(next);
          if (action === "claim") setNotice("Exclusive control acquired.");
          if (action === "release") setNotice("Control released to the queue.");
          if (action === "terminate")
            setNotice("Intervention terminated and session closed.");
          if (action === "resume" && !next.result)
            setNotice(`Resume was not safe: ${next.explanation}`);
        }
        await refreshInbox(true);
      } catch (cause) {
        if (
          !quiet &&
          selectedIntervention.current === intervention.intervention_id
        )
          setError(errorMessage(cause));
        await loadById(intervention.intervention_id, true);
        await refreshInbox(true);
      } finally {
        mutationInFlight.current = false;
        if (!quiet) setPending(null);
      }
    },
    [intervention, operatorId, loadById, refreshInbox],
  );

  const ownerMatches = intervention?.control_owner === `human:${operatorId}`;
  const leaseExpired = intervention ? expired(intervention, now) : false;
  const owned = Boolean(ownerMatches && !leaseExpired);

  useEffect(() => {
    setManualText("");
    setFrame(null);
    setViewportUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
  }, [intervention?.intervention_id, owned]);

  useEffect(() => {
    if (!owned || !intervention) return;
    const timer = window.setInterval(
      () => void transition("heartbeat", true),
      10_000,
    );
    const visible = () => {
      if (document.visibilityState === "visible")
        void loadById(intervention.intervention_id, true);
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [owned, intervention?.intervention_id, transition, loadById]);

  useEffect(() => {
    if (!owned || !intervention) return;
    let cancelled = false;
    const loadFrame = async () => {
      if (frameInFlight.current || mutationInFlight.current) return;
      frameInFlight.current = true;
      try {
        const query = new URLSearchParams({
          expected_lease_version: String(intervention.lease_version),
          operator_id: operatorId,
        });
        const response = await fetch(
          `/runtime/api/v1/interventions/${encodeURIComponent(intervention.intervention_id)}/viewport?${query}`,
          { cache: "no-store" },
        );
        if (!response.ok)
          throw new ApiError(
            (await response.json()) as ErrorBody,
            response.status,
          );
        const sequence = Number(
          response.headers.get("x-replayforge-frame-sequence"),
        );
        const width = Number(
          response.headers.get("x-replayforge-viewport-width"),
        );
        const height = Number(
          response.headers.get("x-replayforge-viewport-height"),
        );
        const nextClientSequence = Number(
          response.headers.get("x-replayforge-next-client-sequence"),
        );
        if (
          ![sequence, width, height, nextClientSequence].every(
            (value) => Number.isSafeInteger(value) && value > 0,
          )
        )
          throw new Error("Live viewport metadata is invalid.");
        const url = URL.createObjectURL(await response.blob());
        if (cancelled) URL.revokeObjectURL(url);
        else {
          setViewportUrl((old) => {
            if (old) URL.revokeObjectURL(old);
            return url;
          });
          setFrame({ sequence, width, height, nextClientSequence });
        }
      } catch (cause) {
        if (!cancelled) {
          if (!(cause instanceof ApiError) || cause.status !== 409) {
            setError(errorMessage(cause));
          }
          await loadById(intervention.intervention_id, true);
        }
      } finally {
        frameInFlight.current = false;
      }
    };
    void loadFrame();
    const timer = window.setInterval(() => void loadFrame(), 2_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    owned,
    intervention?.intervention_id,
    intervention?.lease_version,
    operatorId,
    loadById,
  ]);
  useEffect(
    () => () => {
      if (viewportUrl) URL.revokeObjectURL(viewportUrl);
    },
    [viewportUrl],
  );

  const sendInput = useCallback(
    async (input: HumanInput) => {
      if (
        !intervention ||
        !frame ||
        !owned ||
        mutationInFlight.current ||
        selectedIntervention.current !== intervention.intervention_id
      )
        return false;
      mutationInFlight.current = true;
      setPending("input");
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
              client_sequence: frame.nextClientSequence,
              source_frame_sequence: frame.sequence,
              viewport_width: frame.width,
              viewport_height: frame.height,
              input,
            }),
          },
        );
        await readJson(response);
        if (selectedIntervention.current !== intervention.intervention_id)
          return false;
        setNotice("Input applied to the retained session.");
        setFrame(null);
        setViewportUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return null;
        });
        return true;
      } catch (cause) {
        if (selectedIntervention.current === intervention.intervention_id)
          setError(errorMessage(cause));
        await loadById(intervention.intervention_id, true);
        return false;
      } finally {
        mutationInFlight.current = false;
        setPending(null);
      }
    },
    [intervention, frame, owned, operatorId, loadById],
  );

  function clickViewport(event: MouseEvent<HTMLImageElement>) {
    if (!frame || pending) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    void sendInput({
      kind: "pointer",
      x: Math.min(
        frame.width - 1,
        Math.max(
          0,
          Math.floor(
            ((event.clientX - bounds.left) / bounds.width) * frame.width,
          ),
        ),
      ),
      y: Math.min(
        frame.height - 1,
        Math.max(
          0,
          Math.floor(
            ((event.clientY - bounds.top) / bounds.height) * frame.height,
          ),
        ),
      ),
    });
  }
  async function submitText(event: FormEvent) {
    event.preventDefault();
    if (manualText && (await sendInput({ kind: "text", text: manualText })))
      setManualText("");
  }
  async function directLookup(event: FormEvent) {
    event.preventDefault();
    setPending("lookup");
    await loadById(interventionId.trim());
    setPending(null);
  }

  const claimable = intervention?.status === "open" || leaseExpired;
  const seconds =
    intervention && owned
      ? Math.max(
          0,
          Math.ceil((Date.parse(intervention.lease_expires_at) - now) / 1000),
        )
      : null;
  return (
    <section
      className={
        embedded ? "intervention-console embedded" : "intervention-console"
      }
    >
      <header>
        <div className="mark">R</div>
        <div>
          <p className="eyebrow">ReplayForge</p>
          <h1>Intervention console</h1>
        </div>
        <span className="environment">Local · synthetic data</span>
      </header>
      <section className="operator-bar">
        <div>
          <p className="eyebrow">Operator identity</p>
          <p>
            Exclusive local lease identity; this demo has no authentication
            layer.
          </p>
        </div>
        <label>
          Operator ID
          <input
            value={operatorId}
            onChange={(event) => setOperatorId(event.target.value)}
            disabled={Boolean(ownerMatches)}
            required
          />
        </label>
      </section>
      {error ? (
        <div className="error" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="notice" role="status">
          {notice}
        </div>
      ) : null}
      {resumeResult ? (
        <div className={`result result-${resumeResult.status}`} role="status">
          Replay result: {resumeResult.status.replaceAll("_", " ")}
          {resumeResult.code ? ` · ${resumeResult.code}` : ""}
        </div>
      ) : null}
      <section className="console-grid">
        <aside className="inbox" aria-label="Active replay interventions">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Replay queue</p>
              <h2>Active interventions</h2>
            </div>
            <span className="count">{inbox.length}</span>
          </div>
          <div className="inbox-list">
            {inbox.length ? (
              inbox.map((item) => (
                <button
                  key={item.intervention_id}
                  className={`inbox-item ${intervention?.intervention_id === item.intervention_id ? "selected" : ""}`}
                  onClick={() => void loadById(item.intervention_id)}
                >
                  <span className="inbox-top">
                    <strong>
                      {item.capability_name ?? "Replay intervention"}
                    </strong>
                    <span className={`status status-${item.status}`}>
                      {expired(item, now) ? "expired" : item.status}
                    </span>
                  </span>
                  <span>
                    {item.tenant ?? "unknown tenant"} ·{" "}
                    {item.step_id ?? "between steps"}
                  </span>
                  <small>{item.explanation}</small>
                </button>
              ))
            ) : (
              <p className="empty">No replay sessions need an operator.</p>
            )}
          </div>
          <details className="direct-lookup">
            <summary>Open by intervention ID</summary>
            <form onSubmit={directLookup}>
              <label>
                Intervention ID
                <input
                  value={interventionId}
                  onChange={(event) => setInterventionId(event.target.value)}
                  required
                />
              </label>
              <button disabled={pending === "lookup"}>Open</button>
            </form>
          </details>
        </aside>
        <section className="workspace" aria-live="polite">
          {intervention ? (
            <>
              <div className="context-panel">
                <div>
                  <p className="eyebrow">Paused task</p>
                  <h2>{intervention.capability_name ?? "Intervention"}</h2>
                  <p>{intervention.task_summary}</p>
                </div>
                <div className="context-grid">
                  <span>
                    <b>Reason</b>
                    {intervention.explanation}
                  </span>
                  <span>
                    <b>Trigger</b>
                    <code>{intervention.trigger_code}</code>
                  </span>
                  <span>
                    <b>Step</b>
                    <code>{intervention.step_id ?? "—"}</code>
                  </span>
                  <span>
                    <b>Surface</b>
                    {intervention.application_family} / {intervention.tenant} /{" "}
                    {intervention.surface_route}
                  </span>
                </div>
              </div>
              <div className="session-grid">
                <div className="viewport-panel">
                  <div className="panel-heading">
                    <div>
                      <p className="eyebrow">Same retained browser</p>
                      <h2>Live viewport</h2>
                    </div>
                    <span className={`status status-${intervention.status}`}>
                      {leaseExpired ? "lease expired" : intervention.status}
                    </span>
                  </div>
                  <div className="viewport">
                    {owned && viewportUrl ? (
                      <img
                        src={viewportUrl}
                        alt="Current retained browser viewport; click to send a left-click"
                        onClick={clickViewport}
                      />
                    ) : (
                      <p>
                        {leaseExpired
                          ? "The lease expired. Reclaim it to continue."
                          : ownerMatches
                            ? "Waiting for the latest frame…"
                            : "Claim control to view the live session."}
                      </p>
                    )}
                  </div>
                </div>
                <aside className="controls">
                  <p className="eyebrow">Control lease</p>
                  <dl>
                    <div>
                      <dt>Owner</dt>
                      <dd>{intervention.control_owner}</dd>
                    </div>
                    <div>
                      <dt>Lease</dt>
                      <dd>
                        v{intervention.lease_version}
                        {seconds !== null ? ` · ${seconds}s` : ""}
                      </dd>
                    </div>
                    <div>
                      <dt>Frame</dt>
                      <dd>
                        {frame
                          ? `${frame.sequence} · ${frame.width}×${frame.height}`
                          : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>Run</dt>
                      <dd>{intervention.run_id}</dd>
                    </div>
                  </dl>
                  <div className="actions">
                    <button
                      disabled={Boolean(pending) || !claimable}
                      onClick={() => void transition("claim")}
                    >
                      {leaseExpired ? "Reclaim expired lease" : "Claim control"}
                    </button>
                    <button
                      className="secondary"
                      disabled={Boolean(pending) || !owned}
                      onClick={() => void transition("release")}
                    >
                      Release control
                    </button>
                    <button
                      className="secondary"
                      disabled={Boolean(pending) || !owned}
                      onClick={() => void transition("resume")}
                    >
                      {pending === "resume"
                        ? "Validating…"
                        : "Resume automation"}
                    </button>
                    {!confirmTerminate ? (
                      <button
                        className="danger"
                        disabled={
                          Boolean(pending) ||
                          (!owned && intervention.status !== "open")
                        }
                        onClick={() => setConfirmTerminate(true)}
                      >
                        Terminate session
                      </button>
                    ) : (
                      <div className="confirm">
                        <p>Close the retained session permanently?</p>
                        <button
                          className="danger"
                          onClick={() => void transition("terminate")}
                        >
                          Confirm terminate
                        </button>
                        <button
                          className="secondary"
                          onClick={() => setConfirmTerminate(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="manual-input">
                    <p className="eyebrow">Manual input</p>
                    <p>
                      Click the frame, type into the focused control, or send a
                      navigation key.
                    </p>
                    <form onSubmit={submitText}>
                      <label>
                        Text (not retained)
                        <input
                          value={manualText}
                          onChange={(event) =>
                            setManualText(event.target.value)
                          }
                          maxLength={1000}
                          autoComplete="off"
                          disabled={!owned || !frame || Boolean(pending)}
                        />
                      </label>
                      <button
                        disabled={
                          !owned || !frame || Boolean(pending) || !manualText
                        }
                      >
                        Send
                      </button>
                    </form>
                    <div className="key-input">
                      <label>
                        Navigation key
                        <select
                          value={manualKey}
                          onChange={(event) =>
                            setManualKey(event.target.value as HumanKey)
                          }
                          disabled={!owned || !frame || Boolean(pending)}
                        >
                          {[
                            "Enter",
                            "Escape",
                            "Tab",
                            "Shift+Tab",
                            "Backspace",
                            "Delete",
                            "ArrowUp",
                            "ArrowDown",
                            "ArrowLeft",
                            "ArrowRight",
                          ].map((key) => (
                            <option key={key}>{key}</option>
                          ))}
                        </select>
                      </label>
                      <button
                        disabled={!owned || !frame || Boolean(pending)}
                        onClick={() =>
                          void sendInput({ kind: "key", key: manualKey })
                        }
                      >
                        Send key
                      </button>
                    </div>
                  </div>
                  <p className="boundary">
                    Input is accepted once against the current lease and frame.
                    Typed text is omitted from audit evidence.
                  </p>
                </aside>
              </div>
            </>
          ) : (
            <div className="workspace-empty">
              <p className="eyebrow">Same-session handoff</p>
              <h2>Select an intervention</h2>
              <p>
                Choose a paused replay. Context is visible before claiming; the
                live viewport is restricted to its lease holder.
              </p>
            </div>
          )}
        </section>
      </section>
    </section>
  );
}
