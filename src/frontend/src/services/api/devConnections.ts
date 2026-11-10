// The connection library `ada dev connections` serves live: its state, builds
// from the connection builder's inputs, and the reload events it pushes.
// Local viewer only (`runtime.devConnections()`); see
// src/ada/comms/rest/routes/dev_connections.py.

import { runtime } from "@/runtime/config";

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

export interface DevBuildResult {
  spec_name: string;
  scope: string;
  /** The GLB's blob key in `scope`. */
  key: string;
  inputs: ConnectionInputs;
  log: DevBuildLogLine[];
  counts: { beams?: number; plates?: number; welds?: number };
  generation: number;
  build_ms: number;
}

/** A builder that raised: its message and traceback. */
export class DevBuildError extends Error {
  constructor(
    message: string,
    readonly traceback: string | null,
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
      const body = (await r.json()) as { error?: string; traceback?: string | null };
      throw new DevBuildError(body.error ?? "build failed", body.traceback ?? null);
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
