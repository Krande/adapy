// Browser-first plugin job router with per-user / per-capability worker-pool gating.
//
// Interactive compute workloads default to browser Pyodide/WASM when the plugin
// has a baked wheel in `/wheels/plugin-wheels-manifest.json`. Users granted
// worker-pool access (globally or for the requested capability via GET /api/me)
// can opt into "Run on Worker" for long-duration jobs. Server-only formats
// (e.g. native `.odb` conversion) route to the worker pool when permitted and
// surface a clear capability notice when denied.

import { runtime } from "@/runtime/config";
import type { WorkerAccessPolicy } from "@/services/api/auth";
import type { ScopeUrl } from "@/services/api/client";
import { pluginsApi } from "@/services/api/plugins";
import { getAccessToken } from "@/services/auth/oidc";
import { runPluginJobViaPyodide } from "@/utils/pyodide/pyodide_converter";

import { localArtefactStore, type LocalArtefactStore } from "./localArtefactStore";
import { canUseWorker } from "./workerAccess";

export interface PluginWheelEntry {
  wheel: string;
  entrypoint?: string;
  register?: string;
  deps?: string[];
}

export interface PluginWheelsManifest {
  plugins: Record<string, PluginWheelEntry>;
}

export type ExecutionDecision =
  | { target: "browser" }
  | { target: "worker" }
  | { target: "denied"; reason: string; capability?: string };

export interface BrowserPluginJobOutcome {
  execution: "browser";
  summary: Record<string, unknown>;
  derivedKey?: string;
  localKeys: string[];
}

export interface WorkerPluginJobOutcome {
  execution: "worker";
  job_id: string;
  derived_key: string;
  scope?: string;
}

export type DispatchPluginJobOutcome = BrowserPluginJobOutcome | WorkerPluginJobOutcome;

const PLUGIN_WHEELS_MANIFEST_URL = "/wheels/plugin-wheels-manifest.json";
let cachedManifestPromise: Promise<PluginWheelsManifest> | null = null;

export { canUseWorker };

/** Decide whether a workload should execute in the browser, on the server worker
 * pool, or be denied with an actionable message. */
export function decideExecutionTarget(params: {
  wasmCapable: boolean;
  workerAccess?: WorkerAccessPolicy | null;
  capability?: string | null;
  preferWorker?: boolean;
}): ExecutionDecision {
  const { wasmCapable, workerAccess, capability, preferWorker } = params;
  const workerAllowed = canUseWorker(workerAccess, capability);

  if (preferWorker) {
    if (workerAllowed) return { target: "worker" };
    return {
      target: "denied",
      capability: capability ?? undefined,
      reason: capability
        ? `Worker pool access is required for capability '${capability}'. Ask an administrator to enable it in the Admin Panel.`
        : "Worker pool access is not enabled for your account. Ask an administrator to enable it in the Admin Panel.",
    };
  }

  if (wasmCapable) {
    return { target: "browser" };
  }

  if (workerAllowed) {
    return { target: "worker" };
  }

  return {
    target: "denied",
    capability: capability ?? undefined,
    reason: capability
      ? `This workload requires server worker pool access ('${capability}'). Ask an administrator to enable it in the Admin Panel.`
      : "This workload requires server worker pool access. Ask an administrator to enable it in the Admin Panel.",
  };
}

/** Fetch and cache `/wheels/plugin-wheels-manifest.json`. Returns `{ plugins: {} }`
 * when no plugin wheels are baked into the deployment (e.g. standalone `adapy`). */
export async function loadPluginWheelsManifest(
  fetchImpl: typeof fetch = fetch,
  forceReload = false,
): Promise<PluginWheelsManifest> {
  if (!cachedManifestPromise || forceReload) {
    cachedManifestPromise = (async () => {
      try {
        const resp = await fetchImpl(PLUGIN_WHEELS_MANIFEST_URL, { cache: "no-store" });
        if (!resp.ok) return { plugins: {} };
        const parsed = (await resp.json()) as Partial<PluginWheelsManifest>;
        return { plugins: parsed?.plugins ?? {} };
      } catch {
        return { plugins: {} };
      }
    })();
  }
  return cachedManifestPromise;
}

export async function isPluginWasmCapable(
  pluginId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
  const manifest = await loadPluginWheelsManifest(fetchImpl);
  return Boolean(manifest.plugins[pluginId]?.wheel);
}

/** Dispatch a plugin job to browser Pyodide/WASM (default) or the server worker
 * pool (when explicitly requested and permitted by `workerAccess`). Browser
 * outputs are persisted into `localArtefactStore` for instant viewing and
 * optional explicit Publish to Scope. */
export async function dispatchPluginJob(params: {
  pluginId: string;
  scope: ScopeUrl;
  options: Record<string, unknown>;
  capability?: string;
  derivedKey?: string;
  derivedPrefix?: string;
  preferWorker?: boolean;
  workerAccess?: WorkerAccessPolicy | null;
  store?: LocalArtefactStore;
  onLog?: (msg: string) => void;
  onProgress?: (message: string, fraction: number) => void;
}): Promise<DispatchPluginJobOutcome> {
  const wasmCapable = await isPluginWasmCapable(params.pluginId);
  const decision = decideExecutionTarget({
    wasmCapable,
    workerAccess: params.workerAccess,
    capability: params.capability,
    preferWorker: params.preferWorker,
  });

  if (decision.target === "denied") {
    throw new Error(decision.reason);
  }

  if (decision.target === "worker") {
    const queued = await pluginsApi.pluginJob(
      params.pluginId,
      {
        options: params.options,
        derived_key: params.derivedKey,
        derived_prefix: params.derivedPrefix,
        capability: params.capability,
      },
      { scope: params.scope },
    );
    return { execution: "worker", ...queued };
  }

  const token = getAccessToken();
  const authHeaders = token ? { Authorization: `Bearer ${token}` } : undefined;
  const res = await runPluginJobViaPyodide({
    pluginId: params.pluginId,
    scope: params.scope,
    options: params.options,
    derivedPrefix: params.derivedPrefix,
    apiBase: runtime.apiBase(),
    authHeaders,
    onLog: params.onLog,
    onProgress: params.onProgress,
  });

  const targetStore = params.store ?? localArtefactStore;
  const storedMetas = await targetStore.putMany(params.scope, res.localArtefacts);
  if (params.derivedKey) {
    const summaryBytes = new TextEncoder().encode(JSON.stringify(res.summary));
    await targetStore.put(params.scope, params.derivedKey, summaryBytes, "application/json");
  }
  const localKeys = Array.from(
    new Set([
      ...storedMetas.map((m) => m.key),
      ...(params.derivedKey ? [params.derivedKey] : []),
    ]),
  );

  return {
    execution: "browser",
    summary: res.summary,
    derivedKey: params.derivedKey,
    localKeys,
  };
}
