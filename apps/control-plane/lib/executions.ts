export type Mode = "replay" | "discovery";
export type ContractField = {
  type: string;
  description?: string;
  enum?: unknown[];
};
export type Capability = {
  id: string;
  version: string;
  name: string;
  risk: string;
  tenants: string[];
  inputs: { required: string[]; properties: Record<string, ContractField> };
};
export type Discovery = {
  mode: "discovery";
  goal: string;
  application_family: string;
  tenant: string;
  entry_point: string;
  inputs: Record<string, unknown>;
  validation_tenants: string[];
  max_steps?: number;
  timeout_seconds?: number;
};
export type Catalog = {
  capabilities: Capability[];
  applications: { id: string; tenants: string[]; entry_points: string[] }[];
  presets: {
    name: string;
    capability_id: string | null;
    discovery: Discovery;
  }[];
  discovery_ready: boolean;
};
export type ViewerAccess = { execution_id: string; viewer_token: string };
export type Frame = {
  sequence: number;
  run_id: string;
  phase: string;
  captured_at: string;
  width: number;
  height: number;
  event_sequence: number;
};
export type RunEvent = {
  sequence: number;
  event_type: string;
  step_id: string | null;
  occurred_at: string;
  run_id: string;
  phase: string;
  details: Record<string, unknown>;
};
export type Snapshot = {
  execution_id: string;
  mode: Mode;
  state: string;
  phase: string;
  run_ids: string[];
  frames: Frame[];
  evicted_frames: number;
  events: RunEvent[];
  event_cursor: number;
  first_event_sequence: number | null;
  result: Record<string, unknown> | null;
  retention_seconds: number;
};

export async function json<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok)
    throw new Error(
      body.message ?? body.code ?? `Request failed (${response.status})`,
    );
  return body as T;
}

export function viewerHeaders(access: ViewerAccess) {
  return { "X-Viewer-Token": access.viewer_token };
}

export function fieldValue(value: string, field: ContractField): unknown {
  if (field.type === "string") return value;
  if (field.type === "boolean") return value === "true";
  const parsed: unknown = JSON.parse(value);
  return parsed;
}
