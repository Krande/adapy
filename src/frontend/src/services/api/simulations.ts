// Simulation providers and runs (`routes/simulations.py`).
//
// A simulation provider is something a worker registered that runs simulations behind one
// interface. Its `domain` is the kind of simulation -- one of a closed set (`fea`, `robotics`,
// `cfd`) the server lists with the artefact roles a result of that kind has -- and its `engine`
// is free text. A run is an ordinary worker job with a record that outlives it: started with
// `startRun`, followed by polling `getRun`, and its artefacts are fetched through the blob route
// by their `key`.
//
// WIRE shapes only; `@/state/simulationStore` holds them and polls.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

/** The kinds of simulation the server knows. Open-ended in the type so a newer server's kind
 *  still lists; core's own UI names these three. */
export type SimulationDomainId = "fea" | "robotics" | "cfd" | (string & {});

export interface SimulationArtefactRole {
  readonly id: string;
  readonly description: string;
  /** A finished run of the domain always has a final artefact with this role. */
  readonly required: boolean;
}

export interface SimulationDomain {
  readonly id: SimulationDomainId;
  readonly title: string;
  readonly description: string;
  readonly artefact_roles: readonly SimulationArtefactRole[];
}

export interface SimulationRunKind {
  readonly id: string;
  readonly title: string;
  readonly description: string;
}

/** The part of JSON Schema a provider's parameters use (`ada.simulation.schema`). */
export interface ParameterSchema {
  readonly type?: string | readonly string[];
  readonly title?: string;
  readonly description?: string;
  readonly default?: unknown;
  readonly enum?: readonly unknown[];
  readonly minimum?: number;
  readonly maximum?: number;
  readonly properties?: Readonly<Record<string, ParameterSchema>>;
  readonly required?: readonly string[];
  readonly items?: ParameterSchema;
  readonly [key: string]: unknown;
}

export interface SimulationProviderInfo {
  readonly id: string;
  readonly domain: SimulationDomainId;
  readonly engine: string;
  readonly title: string;
  readonly version: string | null;
  readonly description: string | null;
  readonly kinds: readonly SimulationRunKind[];
  readonly parameters_schema: ParameterSchema;
  /** The worker pool its runs go to. */
  readonly capability: string;
  /** A live worker serves it. A run cannot be started for an offline provider. */
  readonly online: boolean;
  readonly workers: number;
  /** Why a live worker does not serve it, when one withholds it. */
  readonly unavailable_reason?: string;
}

export interface SimulationProvidersResponse {
  readonly domains: readonly SimulationDomain[];
  readonly providers: readonly SimulationProviderInfo[];
}

export type SimulationRunStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface SimulationArtefact {
  readonly name: string;
  /** Storage key in the run's scope; fetched through the blob route. */
  readonly key: string;
  readonly media_type: string;
  readonly role: string;
  readonly size: number | null;
  /** Still being written: fetch it again when `revision` goes up. */
  readonly partial: boolean;
  readonly revision: number;
}

export interface SimulationRun {
  readonly run_id: string;
  readonly provider: { readonly id: string; readonly domain: SimulationDomainId; readonly engine: string; readonly version: string };
  readonly kind: string | null;
  readonly parameters: Readonly<Record<string, unknown>>;
  readonly inputs: readonly string[];
  readonly started_by: { readonly id: string; readonly display: string | null };
  readonly created_at: string;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly status: SimulationRunStatus;
  readonly stage: string;
  readonly progress: number;
  readonly error: string | null;
  readonly job_id: string | null;
  readonly artefacts: readonly SimulationArtefact[];
  readonly summary: Readonly<Record<string, unknown>>;
  /** A cancel was asked for and the run has not ended yet. */
  readonly cancel_requested: boolean;
}

export interface StartSimulationBody {
  readonly provider: string;
  readonly parameters?: Readonly<Record<string, unknown>>;
  /** Storage keys of the run's input files, in the scope the run is started in. */
  readonly inputs?: readonly string[];
  readonly kind?: string;
}

/** Whether a run has not ended: it is worth polling. */
export function isActiveRun(run: Pick<SimulationRun, "status">): boolean {
  return run.status === "queued" || run.status === "running";
}

const scoped = (scope: ScopeUrl) => `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/simulations`;

export const simulationsApi = {
  /** The domains and the providers, grouped by domain; `domain` lists one kind's only. */
  async listProviders(domain?: SimulationDomainId): Promise<SimulationProvidersResponse> {
    const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
    const r = await authedFetch(`${runtime.apiBase()}/simulation/providers${query}`);
    return jsonOrThrow<SimulationProvidersResponse>(r, "listSimulationProviders");
  },

  /** Start a run. The server checks the parameters against the provider's schema first. */
  async startRun(scope: ScopeUrl, body: StartSimulationBody): Promise<SimulationRun> {
    const r = await authedFetch(scoped(scope), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return (await jsonOrThrow<{ run: SimulationRun }>(r, "startSimulation")).run;
  },

  /** The scope's runs, newest first. */
  async listRuns(
    scope: ScopeUrl,
    opts: { limit?: number; provider?: string; domain?: SimulationDomainId } = {},
  ): Promise<SimulationRun[]> {
    const params = new URLSearchParams();
    if (opts.limit) params.set("limit", String(opts.limit));
    if (opts.provider) params.set("provider", opts.provider);
    if (opts.domain) params.set("domain", opts.domain);
    const query = params.toString();
    const r = await authedFetch(`${scoped(scope)}${query ? `?${query}` : ""}`);
    return (await jsonOrThrow<{ runs: SimulationRun[] }>(r, "listSimulations")).runs;
  },

  async getRun(scope: ScopeUrl, runId: string): Promise<SimulationRun> {
    const r = await authedFetch(`${scoped(scope)}/${encodeURIComponent(runId)}`);
    return (await jsonOrThrow<{ run: SimulationRun }>(r, "getSimulation")).run;
  },

  /** Ask a run to stop. `cancelled` is false when it had already ended. */
  async cancelRun(scope: ScopeUrl, runId: string): Promise<{ run: SimulationRun; cancelled: boolean }> {
    const r = await authedFetch(`${scoped(scope)}/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
    return jsonOrThrow<{ run: SimulationRun; cancelled: boolean }>(r, "cancelSimulation");
  },

  /** The blob-route URL of one of a run's artefacts (needs the bearer token when auth is on). */
  artefactUrl(scope: ScopeUrl, artefact: Pick<SimulationArtefact, "key">): string {
    // Spelled as `filesApi.blobUrl` spells it, so the two name the same resource.
    return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(artefact.key)}`;
  },
};

export type SimulationsApi = typeof simulationsApi;
