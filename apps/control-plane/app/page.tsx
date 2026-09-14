"use client";

import { useEffect, useState } from "react";
import ExecutionViewer from "../components/execution-viewer";
import LaunchForm from "../components/launch-form";
import { ViewerAccess } from "../lib/executions";

const STORAGE_KEY = "replayforge-active-execution";

export default function ExecutionConsole() {
  const [access, setAccess] = useState<ViewerAccess | null>(null);
  const [state, setState] = useState("idle");
  useEffect(() => {
    try {
      const stored = sessionStorage.getItem(STORAGE_KEY);
      if (stored) {
        const value = JSON.parse(stored) as ViewerAccess;
        if (
          /^exe_[0-9a-f]{32}$/.test(value.execution_id) &&
          typeof value.viewer_token === "string"
        ) {
          setAccess(value);
          setState("connecting");
        }
      }
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  function start(next: ViewerAccess) {
    setAccess(next);
    setState("running");
    // Only reconnect credentials, never customer inputs, frames or output values.
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  const busy = ["running", "paused", "connecting"].includes(state);
  return (
    <main>
      <header>
        <div className="mark">R</div>
        <div>
          <p className="eyebrow">ReplayForge</p>
          <h1>Execution console</h1>
        </div>
        <a className="environment" href="/interventions">
          Operator inbox
        </a>
      </header>
      <div className="execution-intro">
        <h2>Watch the real work.</h2>
        <p>
          Run a discovered task or start a new discovery. Observe actual browser
          screens, verified steps, and results.
        </p>
        <p className="muted">
          Local operator tool. Screens and results may contain sensitive
          information. Do not expose this console publicly.
        </p>
      </div>
      <div className="execution-layout">
        <LaunchForm
          busy={busy}
          onStart={start}
          refreshKey={state === "success" ? access?.execution_id : undefined}
        />
        {access ? (
          <div>
            <ExecutionViewer
              key={access.execution_id}
              access={access}
              onState={setState}
            />
            {!busy && (
              <button
                className="secondary dismiss-view"
                onClick={() => {
                  setAccess(null);
                  setState("idle");
                  sessionStorage.removeItem(STORAGE_KEY);
                }}
              >
                Close this view
              </button>
            )}
          </div>
        ) : (
          <section className="execution-empty">
            <h2>Your execution appears here</h2>
            <p>
              Choose a task, supply inputs or use the configured demo defaults,
              then select Run and watch.
            </p>
            <p>
              Replay is model-free. Discovery uses the configured model and
              validates the resulting capability before publication.
            </p>
          </section>
        )}
      </div>
    </main>
  );
}
