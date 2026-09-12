// Worker fleet: the live worker roster, staleness pruning, per-image
// package manifests, and the derived-blob compression sweep.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail, type ScopeUrl } from "./client";

export interface WorkerPackage {
  name: string;
  version: string | null;
  build: string | null;
  channel: string | null;
}

/** Per-scope state of a compression-sweep background task. */
export interface CompressionSweepState {
  started_at: number;
  completed_at: number | null;
  last_update: number;
  total: number;
  processed: number;
  compressed: number;
  already_gzipped: number;
  bytes_before: number;
  bytes_after: number;
  errors: { key: string; error: string }[];
  error: string | null;
  cancelled: boolean;
  /** Filename currently being compressed, if any. */
  current_key: string | null;
  /** Server marks ``true`` when ``completed_at`` is null and the
   * ``last_update`` heartbeat is older than 90 s — most likely the
   * viewer pod restarted mid-sweep and the BackgroundTask was lost. */
  orphaned: boolean;
}

/** One advertised conversion: a source extension and every target it can produce. */
export interface WorkerConversion {
  from: string;
  to: string[];
}

/** One advertised @utility spec. Only `name`/`description` are rendered; extra keys are tolerated. */
export interface WorkerUtilitySpec {
  name?: string;
  description?: string;

  [key: string]: unknown;
}

/** One worker pod's self-reported registration entry. */
export interface WorkerEntry {
  worker_id: string;
  image_tag: string | null;
  capabilities: string[];
  started_at: number;
  last_heartbeat: number;
  online: boolean;
  // The backend registration payload (worker.py) also advertises these; they are optional
  // because the type historically dropped them and older workers may omit them.
  source_exts?: string[];
  conversions?: WorkerConversion[];
  utilities?: WorkerUtilitySpec[];
}

export const workersApi = {
  /** Admin: the captured package manifest ("pixi list") for a worker image
   * tag — linked from a convert audit row via its worker_image_tag. */
  async adminWorkerPackages(imageTag: string): Promise<{
    worker_image_tag: string;
    packages: WorkerPackage[];
    captured_at: string | null;
  }> {
    const url = `${runtime.apiBase()}/admin/worker-packages/${encodeURIComponent(imageTag)}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminWorkerPackages");
  },

  /** Admin: kick off a background sweep that scans the scope for
   * gzip-compressible source files (.ifc / .step / .sif / etc.)
   * whose stored bytes aren't gzipped, and rewrites each with
   * Content-Encoding: gzip. Returns 202 immediately — poll
   * ``adminCompressionStatus`` for progress. */
  async adminStartCompressionSweep(scope: ScopeUrl): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/storage/${encodeURIComponent(scope)}/compress-uncompressed`,
      { method: "POST" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminStartCompressionSweep(${scope})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: snapshot of the in-flight + recently-completed
   * compression sweeps, keyed by scope. */
  async adminCompressionStatus(): Promise<{
    scopes: Record<string, CompressionSweepState>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/storage/compression-status`,
    );
    return jsonOrThrow(r, "adminCompressionStatus");
  },

  /** Admin: snapshot of every worker pod that recently checked in.
   * The ``online`` flag is true when ``last_heartbeat`` is within
   * ``stale_after_s`` of ``now`` (both reported by the server so the
   * client doesn't depend on local clock skew). */
  async adminListWorkers(): Promise<{
    workers: WorkerEntry[];
    now: number;
    stale_after_s: number;
  }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/workers`);
    return jsonOrThrow(r, "adminListWorkers");
  },

  /** Admin: drop every currently-offline worker registry entry (a live pod re-registers within a
   * heartbeat). Returns the number pruned. */
  async adminPruneWorkers(): Promise<{ pruned: number }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/workers/prune`, {
      method: "POST",
    });
    return jsonOrThrow(r, "adminPruneWorkers");
  },
};
