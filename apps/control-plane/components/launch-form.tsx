"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  Catalog,
  Discovery,
  Mode,
  ViewerAccess,
  fieldValue,
  json,
} from "../lib/executions";

export default function LaunchForm({
  busy,
  onStart,
  refreshKey,
}: {
  busy: boolean;
  onStart: (access: ViewerAccess) => void;
  refreshKey?: string;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [mode, setMode] = useState<Mode>("replay");
  const [choice, setChoice] = useState(0);
  const [tenant, setTenant] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [application, setApplication] = useState("");
  const [entry, setEntry] = useState("");
  const [goal, setGoal] = useState("");
  const [inputJson, setInputJson] = useState("{}");
  const [validationTenants, setValidationTenants] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [preset, setPreset] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    fetch("/runtime/api/v1/executions/catalog", {
      cache: "no-store",
      signal: abort.signal,
    })
      .then(json<Catalog>)
      .then((data) => {
        setCatalog(data);
        setTenant(
          data.capabilities[0]?.tenants[0] ??
            data.applications[0]?.tenants[0] ??
            "",
        );
        setApplication(data.applications[0]?.id ?? "");
        setEntry(data.applications[0]?.entry_points[0] ?? "");
      })
      .catch((cause) => {
        if (!abort.signal.aborted) setError(String(cause));
      });
    return () => abort.abort();
  }, [refreshKey]);

  const capability = catalog?.capabilities[choice];
  const app = catalog?.applications.find((item) => item.id === application);
  const defaults = catalog?.presets.find(
    (item) => item.capability_id === capability?.id,
  );

  function applyDiscovery(item: Discovery) {
    setApplication(item.application_family);
    setTenant(item.tenant);
    setEntry(item.entry_point);
    setGoal(item.goal);
    setInputJson(JSON.stringify(item.inputs, null, 2));
    setValidationTenants(item.validation_tenants);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      let execution: Record<string, unknown>;
      if (mode === "replay") {
        if (!capability) throw new Error("Choose a published capability.");
        const inputs = Object.fromEntries(
          Object.entries(capability.inputs.properties)
            .filter(
              ([key]) =>
                capability.inputs.required.includes(key) ||
                values[key] !== undefined,
            )
            .map(([key, field]) => [key, fieldValue(values[key] ?? "", field)]),
        );
        execution = {
          mode,
          capability_id: capability.id,
          version: capability.version,
          tenant,
          inputs,
        };
      } else {
        const inputs: unknown = JSON.parse(inputJson);
        if (!inputs || typeof inputs !== "object" || Array.isArray(inputs))
          throw new Error(
            "Inputs must be a JSON object with named parameters.",
          );
        execution = {
          mode,
          goal,
          application_family: application,
          entry_point: entry,
          tenant,
          inputs,
          validation_tenants: validationTenants,
          max_steps: 50,
          timeout_seconds: 600,
        };
      }
      const result = await fetch("/runtime/api/v1/executions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ execution }),
      }).then(json<ViewerAccess>);
      onStart(result);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not start execution.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="launch-panel" onSubmit={submit}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Execution</p>
          <h2>Run and watch</h2>
        </div>
      </div>
      <fieldset disabled={busy || pending || !catalog}>
        <div className="mode-switch" aria-label="Execution mode">
          {(["replay", "discovery"] as Mode[]).map((item) => (
            <button
              type="button"
              key={item}
              aria-pressed={mode === item}
              onClick={() => {
                setMode(item);
                setError(null);
                setTenant(
                  item === "replay"
                    ? (capability?.tenants[0] ?? "")
                    : (app?.tenants[0] ?? ""),
                );
              }}
            >
              {item === "replay" ? "Replay saved task" : "Discover new task"}
            </button>
          ))}
        </div>
        <p className="muted">
          {mode === "replay"
            ? "Deterministic execution · no model calls"
            : "Model-driven discovery · human help only when blocked · automatic publication after validation"}
        </p>
        {mode === "replay" ? (
          <>
            <label>
              Capability
              <select
                value={choice}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setChoice(next);
                  setValues({});
                  setTenant(catalog?.capabilities[next]?.tenants[0] ?? "");
                }}
              >
                {catalog?.capabilities.map((item, index) => (
                  <option key={`${item.id}:${item.version}`} value={index}>
                    {item.name} · v{item.version}
                  </option>
                ))}
              </select>
            </label>
            {!capability && (
              <p>No published capabilities. Start with discovery.</p>
            )}
            {capability && (
              <p className="muted">
                Risk: {capability.risk.replaceAll("_", " ")}
              </p>
            )}
            {Object.entries(capability?.inputs.properties ?? {}).map(
              ([key, field]) => (
                <label key={key}>
                  {key.replaceAll("_", " ")}
                  {capability?.inputs.required.includes(key) ? " *" : ""}
                  {field.type === "boolean" ? (
                    <select
                      value={values[key] ?? ""}
                      required={capability?.inputs.required.includes(key)}
                      onChange={(event) =>
                        setValues({ ...values, [key]: event.target.value })
                      }
                    >
                      <option value="">Choose…</option>
                      <option value="true">True</option>
                      <option value="false">False</option>
                    </select>
                  ) : (
                    <input
                      value={values[key] ?? ""}
                      autoComplete="off"
                      required={capability?.inputs.required.includes(key)}
                      onChange={(event) =>
                        setValues({ ...values, [key]: event.target.value })
                      }
                    />
                  )}
                  {field.description && <small>{field.description}</small>}
                </label>
              ),
            )}
            {defaults && (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setValues(
                    Object.fromEntries(
                      Object.entries(defaults.discovery.inputs).map(
                        ([key, value]) => [
                          key,
                          typeof value === "string"
                            ? value
                            : JSON.stringify(value),
                        ],
                      ),
                    ),
                  );
                }}
              >
                Use demo defaults
              </button>
            )}
          </>
        ) : (
          <>
            <label>
              Optional task preset
              <select
                value={preset}
                onChange={(event) => {
                  setPreset(event.target.value);
                  const item = catalog?.presets.find(
                    (candidate) => candidate.name === event.target.value,
                  );
                  if (item) applyDiscovery(item.discovery);
                }}
              >
                <option value="">Custom task</option>
                {catalog?.presets.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Application
              <select
                value={application}
                onChange={(event) => {
                  const next = catalog?.applications.find(
                    (item) => item.id === event.target.value,
                  );
                  setApplication(event.target.value);
                  setEntry(next?.entry_points[0] ?? "");
                  setTenant(next?.tenants[0] ?? "");
                  setValidationTenants([]);
                }}
              >
                {catalog?.applications.map((item) => (
                  <option key={item.id}>{item.id}</option>
                ))}
              </select>
            </label>
            <label>
              Entry point
              <select
                value={entry}
                onChange={(event) => setEntry(event.target.value)}
              >
                {app?.entry_points.map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </label>
            <label>
              Task goal
              <textarea
                value={goal}
                minLength={10}
                maxLength={1000}
                required
                rows={5}
                onChange={(event) => setGoal(event.target.value)}
              />
            </label>
            <label>
              Inputs · JSON object
              <textarea
                value={inputJson}
                required
                rows={6}
                spellCheck={false}
                onChange={(event) => setInputJson(event.target.value)}
              />
            </label>
            <div className="validation-options">
              <p>Also validate on</p>
              {app?.tenants
                .filter((item) => item !== tenant)
                .map((item) => (
                  <label key={item}>
                    <input
                      type="checkbox"
                      checked={validationTenants.includes(item)}
                      onChange={(event) =>
                        setValidationTenants(
                          event.target.checked
                            ? [...validationTenants, item]
                            : validationTenants.filter(
                                (value) => value !== item,
                              ),
                        )
                      }
                    />
                    {item}
                  </label>
                ))}
            </div>
            <p className="muted">
              Up to 50 discovery steps / 10 minutes. Validation performs the
              task again in fresh sessions. Screenshots and supplied inputs may
              be sent to the configured model provider.
            </p>
            {catalog && !catalog.discovery_ready && (
              <p role="status">
                Discovery provider is not ready. Replay remains available.
              </p>
            )}
          </>
        )}
        <label>
          Tenant
          <select
            value={tenant}
            onChange={(event) => setTenant(event.target.value)}
            required
          >
            {(mode === "replay" ? capability?.tenants : app?.tenants)?.map(
              (item) => (
                <option key={item}>{item}</option>
              ),
            )}
          </select>
        </label>
        <button
          className="start-button"
          disabled={mode === "discovery" && !catalog?.discovery_ready}
        >
          {pending ? "Starting…" : "Run and watch"}
        </button>
      </fieldset>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
