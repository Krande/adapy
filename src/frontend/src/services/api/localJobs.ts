// The local job queue of a server with no job queue of its own: it runs every job in its own
// environment, so how many run at once and how much CPU they share is the user's setting.
// GET /api/local/jobs, PUT /api/local/jobs/settings (ada.comms.rest.routes.local_jobs).

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

export interface LocalJobsView {
  max_parallel: number;
  /** The budget as set ("4", "0.5", "50%"); null = every core. */
  cpu_budget: string | null;
  cpu_budget_cores: number;
  limited: boolean;
  host_cores: number;
  default_max_parallel: number;
  running: { job_id: string; kind: string; threads: number; light: boolean }[];
  queued: { job_id: string; kind: string; light: boolean }[];
}

export interface LocalJobsSettings {
  /** null returns the field to its default. */
  max_parallel?: number | null;
  cpu_budget?: string | number | null;
}

export const localJobsApi = {
  async get(): Promise<LocalJobsView> {
    const r = await authedFetch(`${runtime.apiBase()}/local/jobs`);
    return jsonOrThrow<LocalJobsView>(r, "localJobs.get");
  },

  async set(settings: LocalJobsSettings): Promise<LocalJobsView> {
    const r = await authedFetch(`${runtime.apiBase()}/local/jobs/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    // jsonOrThrow puts the server's sentence ("max_parallel must be between 1 and 256") in the
    // error's message, which is what Settings shows.
    return jsonOrThrow<LocalJobsView>(r, "localJobs.set");
  },
};
