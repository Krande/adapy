// The connection library `ada dev connections` serves live: its state, builds
// from the connection builder's inputs, and the reload events it pushes.
// Local viewer only (`runtime.devConnections()`); see
// src/ada/comms/rest/routes/dev_connections.py.

import { runtime } from "@/runtime/config";

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

const base = () => `${runtime.apiBase()}/dev/connections`;

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

  async reload(): Promise<DevConnectionsState> {
    return jsonOrThrow<DevConnectionsState>(await authedFetch(`${base()}/reload`, { method: "POST" }), "devConnections.reload");
  },

  async restart(): Promise<DevConnectionsState> {
    return jsonOrThrow<DevConnectionsState>(
      await authedFetch(`${base()}/restart`, { method: "POST" }),
      "devConnections.restart",
    );
  },

  /** Follow the library's state; returns the unsubscribe. EventSource reconnects on its own. */
  subscribe(onEvent: (e: DevConnectionsEvent) => void, onError?: () => void): () => void {
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
