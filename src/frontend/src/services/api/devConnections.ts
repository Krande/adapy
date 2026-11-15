// The connection builder's server: its specs, builds from the builder's inputs,
// and the updates it is told about. Two backends answer in one shape:
//
//   * `ada dev connections` (a local viewer, `runtime.devConnections()`): a library
//     served live, its reloads pushed as events (routes/dev_connections.py);
//   * a deployed studio: the specs the live workers' code registers, offered in the
//     current scope as the admin assigned them, and built on the workers' pools
//     (routes/connections.py). Nothing pushes there, so the state is polled.

import { runtime } from "@/runtime/config";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";

import type { ContactRunDoc } from "@/services/clash/contactRuns";

import { authedFetch, jsonOrThrow } from "./client";
import type { ComponentSpecSchema } from "./components";

/** Per-role inputs, keyed by lowercase role name (`incoming`, `landing`). */
export type ConnectionInputs = Record<string, Record<string, unknown>>;

export interface DevConnectionSpec {
  name: string;
  schema: ComponentSpecSchema;
  /** The spec's declared defaults, with every field they leave out filled in. */
  defaults: ConnectionInputs;
  module: string | null;
  source: { file: string; line: number } | null;
  /** Registered by the library's own modules (not by a package it imports). */
  in_library: boolean;
  /** Deployed studio only: the worker pool that builds it (null: core's own, any worker). */
  capability?: string | null;
  /** Deployed studio only: its pool is online but not taking jobs. */
  unavailable?: boolean;
}

export interface DevConnectionsError {
  error: string;
  traceback: string | null;
}

export type DevConnectionsStatus = "starting" | "restarting" | "reloading" | "ready" | "error";

export interface DevConnectionsState {
  library: { name: string; config: string; root: string; watch: string[] };
  status: DevConnectionsStatus;
  /** Bumped on every (re)load: a build from an older generation is stale. */
  generation: number;
  specs: DevConnectionSpec[];
  /** Why the library is not loaded, while `status` is "error". */
  error: DevConnectionsError | null;
  /** The files whose change caused the latest reload, relative to the library. */
  changed: string[];
  updated_at: number;
}

export interface DevConnectionsEvent extends DevConnectionsState {
  type: "state";
  reason: string | null;
}

export interface DevBuildLogLine {
  level: string;
  logger: string;
  message: string;
}

/** A member of the built joint, as the GLB's object metadata describes it, plus its role. */
export interface DevJointMember {
  name: string;
  type: string; // "Beam" | "Plate"
  role: string | null;
  section?: { name: string | null; type: string | null; h: number | null; w_top: number | null } | null;
  thickness?: number | null;
  material?: { name?: string | null } | null;
  length?: number;
  /** Boolean cuts on the member (a stub's, or the builder's trims). */
  booleans: number;
  /** A beam's axis (n1, n2), in the GLB's model frame. */
  axis?: [number[], number[]];
  /** A plate's centroid, in the GLB's model frame. */
  centre?: number[];
}

/** A weld of the built joint, as the GLB's object metadata describes it. */
export interface DevJointWeld {
  name: string;
  weld_type: string | null;
  throat: number | null;
  leg1: number | null;
  leg2: number | null;
  sided: unknown;
  intermittent: { pitch: number; length_on: number; length_off: number } | null;
  /** The members it says it joins. */
  members: string[];
  /** What it actually touches in the built GLB (members and other welds). */
  touches: string[];
  /** Whether it touches every member it says it joins; null where contacts were not measured. */
  joins_its_members: boolean | null;
  /** Where it meets its members (else its sweep curve's middle), in the GLB's model frame. */
  at: number[] | null;
  /** Its weld symbol (ISO 2553, `ada.api.weld_symbols`) as an SVG icon stroked in `currentColor`. */
  symbol_svg: string | null;
}

export interface DevBuildResult {
  spec_name: string;
  scope: string;
  /** The GLB's blob key in `scope`. */
  key: string;
  inputs: ConnectionInputs;
  log: DevBuildLogLine[];
  counts: { beams?: number; plates?: number; welds?: number };
  properties: { members: DevJointMember[]; welds: DevJointWeld[] };
  /** The joint's clash: the members' measured contact in this GLB, as a convex-contact run (its
   *  member ids are this GLB's element ids). Null where it could not be measured. */
  clash_run: ContactRunDoc | null;
  /** Why `clash_run` is null. */
  clash_note: string | null;
  generation: number;
  build_ms: number;
}

/** The detail offered as a simulation subject (`POST /dev/connections/subject`): a weld assembly
 *  stored in the caller's scope. */
export interface DevSubjectResult {
  scope: string;
  /** The subject file's blob key in `scope`. */
  key: string;
  /** The subject kind (`SUBJECT_KINDS` on the server): `"weld-assembly"`. */
  kind: string;
  welds: number;
  /** What a person calls it: `"<spec> (<n> welds)"`. */
  label: string;
}

/** The detail has no welds: there is nothing to simulate (HTTP 409). */
export class DevSubjectRefused extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DevSubjectRefused";
  }
}

/** A builder that raised: its message and traceback (from the library's own code). `declined`
 *  when it raised NotApplicable -- it refused these members, which is not a crash. */
export class DevBuildError extends Error {
  constructor(
    message: string,
    readonly traceback: string | null,
    readonly declined: boolean = false,
  ) {
    super(message);
    this.name = "DevBuildError";
  }
}

/** Which server the builder talks to. */
export type ConnectionsBackend = "dev" | "deployed";

export function connectionsBackend(): ConnectionsBackend {
  return runtime.devConnections() ? "dev" : "deployed";
}

/** How often a deployed studio asks again what is offered (a worker came or went, new code). */
export const DEPLOYED_POLL_MS = 30_000;

const base = () =>
  connectionsBackend() === "dev"
    ? `${runtime.apiBase()}/dev/connections`
    : `${runtime.apiBase()}/scopes/${encodeURIComponent(scopeUrlPart(useScopeStore.getState().current))}/connections`;

export const devConnectionsApi = {
  async state(): Promise<DevConnectionsState> {
    return jsonOrThrow<DevConnectionsState>(await authedFetch(base()), "devConnections.state");
  },

  /** Build `specName` from `inputs`. Throws {@link DevBuildError} when the builder raises. */
  async build(specName: string, inputs: ConnectionInputs): Promise<DevBuildResult> {
    const r = await authedFetch(`${base()}/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec_name: specName, inputs }),
    });
    if (r.status === 422) {
      const body = (await r.json()) as { error?: string; traceback?: string | null; declined?: boolean };
      throw new DevBuildError(body.error ?? "build failed", body.traceback ?? null, body.declined === true);
    }
    return jsonOrThrow<DevBuildResult>(r, `devConnections.build(${specName})`);
  },

  /** Offer the detail built from `inputs` as a simulation subject: the server stores its weld
   *  assembly. Throws {@link DevSubjectRefused} when it has no welds and {@link DevBuildError} when
   *  the builder raises. */
  async subject(specName: string, inputs: ConnectionInputs): Promise<DevSubjectResult> {
    const r = await authedFetch(`${base()}/subject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec_name: specName, inputs }),
    });
    if (r.status === 409) {
      const body = (await r.json().catch(() => ({}))) as { detail?: string };
      throw new DevSubjectRefused(body.detail ?? "the detail has no welds");
    }
    if (r.status === 422) {
      const body = (await r.json()) as { error?: string; traceback?: string | null; declined?: boolean };
      throw new DevBuildError(body.error ?? "build failed", body.traceback ?? null, body.declined === true);
    }
    return jsonOrThrow<DevSubjectResult>(r, `devConnections.subject(${specName})`);
  },

  async reload(): Promise<DevConnectionsState> {
    // A deployed studio has no library to reload: asking again is what reloading means there.
    if (connectionsBackend() === "deployed") return devConnectionsApi.state();
    return jsonOrThrow<DevConnectionsState>(await authedFetch(`${base()}/reload`, { method: "POST" }), "devConnections.reload");
  },

  async restart(): Promise<DevConnectionsState> {
    return jsonOrThrow<DevConnectionsState>(
      await authedFetch(`${base()}/restart`, { method: "POST" }),
      "devConnections.restart",
    );
  },

  /** Follow the library's state; returns the unsubscribe. EventSource reconnects on its own. On a
   *  deployed studio the state is asked for now, every {@link DEPLOYED_POLL_MS}, and whenever the
   *  scope changes; an event is emitted when what is offered changed (its generation). */
  subscribe(onEvent: (e: DevConnectionsEvent) => void, onError?: () => void): () => void {
    if (connectionsBackend() === "deployed") return pollDeployed(onEvent, onError);
    const source = new EventSource(`${base()}/events`);
    source.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data) as DevConnectionsEvent);
      } catch (err) {
        console.warn("dev-connections: unreadable event", err);
      }
    };
    if (onError) source.onerror = onError;
    return () => source.close();
  },
};

function pollDeployed(onEvent: (e: DevConnectionsEvent) => void, onError?: () => void): () => void {
  let last: { generation: number; scope: string } | null = null;
  let stopped = false;
  const ask = async () => {
    const scope = scopeUrlPart(useScopeStore.getState().current);
    try {
      const state = await devConnectionsApi.state();
      if (stopped) return;
      if (!last || last.generation !== state.generation || last.scope !== scope) {
        last = { generation: state.generation, scope };
        onEvent({ ...state, type: "state", reason: null });
      }
    } catch (err) {
      console.warn("connections: could not read the offered specs", err);
      if (!stopped) onError?.();
    }
  };
  void ask();
  const timer = setInterval(() => void ask(), DEPLOYED_POLL_MS);
  const unsubscribeScope = useScopeStore.subscribe((s, prev) => {
    if (scopeUrlPart(s.current) !== scopeUrlPart(prev.current)) void ask();
  });
  return () => {
    stopped = true;
    clearInterval(timer);
    unsubscribeScope();
  };
}
