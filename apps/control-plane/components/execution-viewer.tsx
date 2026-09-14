"use client";

import { useEffect, useRef, useState } from "react";
import InterventionConsole from "../app/interventions/console";
import {
  Frame,
  RunEvent,
  Snapshot,
  ViewerAccess,
  json,
  viewerHeaders,
} from "../lib/executions";

export default function ExecutionViewer({
  access,
  onState,
}: {
  access: ViewerAccess;
  onState: (state: string) => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [image, setImage] = useState<{ url: string; sequence: number } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const cursor = useRef(0);
  const stateCallback = useRef(onState);
  stateCallback.current = onState;

  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await fetch(
          `/runtime/api/v1/executions/${access.execution_id}?after=${cursor.current}`,
          {
            cache: "no-store",
            headers: viewerHeaders(access),
            signal: abort.signal,
          },
        );
        if (response.status === 404) {
          setError(
            "This execution view expired or the runtime restarted. Start a new run to watch it again.",
          );
          setSnapshot(null);
          stateCallback.current("expired");
          return;
        }
        const next = await json<Snapshot>(response);
        if (abort.signal.aborted) return;
        setSnapshot(next);
        setError(null);
        stateCallback.current(next.state);
        setEvents((previous) => [...previous, ...next.events].slice(-2000));
        cursor.current = next.event_cursor;
      } catch (cause) {
        if (!abort.signal.aborted)
          setError(
            `Connection interrupted; the run is not restarted. ${String(cause)}`,
          );
      }
      if (!abort.signal.aborted) timer = setTimeout(poll, 1000);
    }
    void poll();
    const ageTimer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      abort.abort();
      clearTimeout(timer);
      clearInterval(ageTimer);
    };
  }, [access]);

  const frames = snapshot?.frames ?? [];
  const latest = frames.at(-1);
  const frame =
    selected === null
      ? latest
      : frames.find((item) => item.sequence === selected);
  const sequence = frame?.sequence;
  const historic = selected !== null;
  const selectedIndex = frame
    ? frames.findIndex((item) => item.sequence === frame.sequence)
    : -1;
  const intervention =
    snapshot?.state === "paused" &&
    typeof snapshot.result?.intervention_id === "string"
      ? snapshot.result.intervention_id
      : null;

  useEffect(() => {
    if (sequence === undefined) {
      setImage(null);
      return;
    }
    const abort = new AbortController();
    let objectUrl: string | null = null;
    setFrameError(null);
    setImage(null);
    fetch(
      `/runtime/api/v1/executions/${access.execution_id}/frames/${sequence}`,
      {
        cache: "no-store",
        headers: viewerHeaders(access),
        signal: abort.signal,
      },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            response.status === 410
              ? "This screen has expired from temporary history. Choose another screen or return Live."
              : "Screen unavailable.",
          );
        const blob = await response.blob();
        if (abort.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({ url: objectUrl, sequence });
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setFrameError(String(cause));
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [access, sequence]);

  function inspect(target: Frame | undefined) {
    if (target) setSelected(target.sequence);
  }

  return (
    <section className="execution-panel" aria-label="Execution viewer">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">
            {snapshot?.mode ?? "Execution"} · {snapshot?.phase ?? "Starting"}
          </p>
          <h2>{historic ? "Earlier screen · read-only" : "Live execution"}</h2>
        </div>
        <span className="status">{snapshot?.state ?? "Connecting"}</span>
      </div>
      <p className="muted execution-id">{access.execution_id}</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {snapshot?.mode === "replay" && (
        <nav className="history-controls" aria-label="Replay screen history">
          <button
            type="button"
            disabled={selectedIndex <= 0}
            onClick={() => inspect(frames[selectedIndex - 1])}
          >
            Back
          </button>
          <button
            type="button"
            disabled={
              !historic ||
              selectedIndex < 0 ||
              selectedIndex >= frames.length - 1
            }
            onClick={() => inspect(frames[selectedIndex + 1])}
          >
            Next
          </button>
          <button
            type="button"
            aria-pressed={!historic}
            onClick={() => setSelected(null)}
          >
            Live
          </button>
          <span>
            {frame ? `Screen ${frame.sequence}` : "Waiting for a screen"}
          </span>
        </nav>
      )}
      {historic && (
        <p className="notice">
          You are inspecting history. The real run is unchanged. Return Live to
          view or operate a paused session.
        </p>
      )}
      {frame && (
        <p className="frame-age">
          Captured{" "}
          {Math.max(
            0,
            Math.floor((now - Date.parse(frame.captured_at)) / 1000),
          )}
          s ago · {frame.phase} · screen {frame.sequence}
        </p>
      )}
      <div
        hidden={Boolean(intervention) && !historic}
        className="watch-screen"
        aria-label={historic ? "Historical screen" : "Live screen"}
      >
        {image && image.sequence === sequence ? (
          <img
            src={image.url}
            alt={
              historic
                ? "Earlier replay screen, read-only"
                : "Actual execution browser screen, read-only"
            }
          />
        ) : (
          <p>
            {historic && !frame
              ? "This historical screen has expired. Return Live to continue watching."
              : (frameError ??
                (snapshot?.state === "running"
                  ? "Waiting for the next actual browser frame…"
                  : "No screen available."))}
          </p>
        )}
      </div>
      {intervention && (
        <div hidden={historic}>
          <InterventionConsole
            key={intervention}
            initialInterventionId={intervention}
            embedded
          />
        </div>
      )}
      <p className="muted">
        Frames update at execution boundaries, not as continuous video.
        {snapshot?.mode === "replay"
          ? ` ${snapshot.evicted_frames} earlier screens expired. History is temporary.`
          : " Discovery keeps only the latest screen; human control is available only when blocked."}
      </p>
      {snapshot?.result && snapshot.state !== "paused" && (
        <section className="final-result" aria-label="Final result">
          <h3>Final result</h3>
          <pre>{JSON.stringify(snapshot.result, null, 2)}</pre>
          <p className="muted">
            This view expires {snapshot.retention_seconds / 60} minutes after
            completion. Screens are not saved to disk.
          </p>
        </section>
      )}
      <details className="step-timeline" open>
        <summary>Step timeline · {events.length} events</summary>
        <ol>
          {events.map((event) => (
            <li key={event.sequence}>
              <span className="event-sequence">{event.sequence}</span>
              <div>
                <strong>{event.event_type.replaceAll("_", " ")}</strong>
                {event.step_id && <span> · {event.step_id}</span>}
                <small>
                  {event.phase} ·{" "}
                  {new Date(event.occurred_at).toLocaleTimeString()}
                </small>
                <details>
                  <summary>Sanitized details</summary>
                  <pre>{JSON.stringify(event.details, null, 2)}</pre>
                </details>
              </div>
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
