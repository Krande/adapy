// Browser-first plugin job router with per-user / per-capability worker-pool gating.
//
// Interactive compute workloads default to browser Pyodide/WASM when the plugin
// has a baked wheel in `/wheels/plugin-wheels-manifest.json`. Users granted
// worker-pool access (globally or for the requested capability via GET /api/me)
// can opt into "Run on Worker" for long-duration jobs; only that explicit choice
// is judged by the server's policy. A plugin with no browser path runs on the
// worker pool for everyone.

import { runtime } from "@/runtime/config";
import type { WorkerAccessPolicy } from "@/services/api/auth";
import type { ScopeUrl } from "@/services/api/client";
import { pluginsApi } from "@/services/api/plugins";
import { getAccessToken } from "@/services/auth/oidc";
import { runPluginJobViaPyodide } from "@/utils/pyodide/pyodide_converter";

import { engineOrder, type ExecEngine } from "./executionPolicy";
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
  // explicit: the user chose the worker over the browser (sent as X-Ada-Run-On).
  | { target: "worker"; explicit?: boolean }
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
 * pool, or be denied with an actionable message.
 *
 * ``order`` is the execution policy's order for this plugin ("plugin_job", see
 * executionPolicy.ts: the allowed engines, worker access applied, the user's
 * preference first); without one the browser comes first. ``preferWorker`` is an
 * explicit per-job "Run on worker". */
export function decideExecutionTarget(params: {
  wasmCapable: boolean;
  workerAccess?: WorkerAccessPolicy | null;
  capability?: string | null;
  preferWorker?: boolean;
  order?: readonly ExecEngine[];
}): ExecutionDecision {
  const { wasmCapable, workerAccess, capability, preferWorker } = params;
  const workerAllowed = canUseWorker(workerAccess, capability);
  const order = (params.order ?? (["pyodide", "worker"] as const)).filter(
    (e) => e !== "worker" || workerAllowed,
  );

  // No browser path: nothing to choose between, so the worker runs it for
  // everyone (the server's policies judge only an explicit worker choice).
  if (!wasmCapable) return { target: "worker" };

  // Choosing the worker is only a choice when the browser could run it.
  if (preferWorker) {
    if (workerAllowed && order.includes("worker")) return { target: "worker", explicit: true };
    if (!workerAllowed) {
      return {
        target: "denied",
        capability: capability ?? undefined,
        reason: capability
          ? `Worker pool access is required for capability '${capability}'. Ask an administrator to enable it in the Admin Panel.`
          : "Worker pool access is not enabled for your account. Ask an administrator to enable it in the Admin Panel.",
      };
    }
    return {
      target: "denied",
      capability: capability ?? undefined,
      reason:
        "The execution policy does not allow the server worker pool for this plugin. Ask an administrator to change it in the Admin Panel (Execution).",
    };
  }

  const first = order.find((e) => e === "pyodide" || e === "worker");
  if (first === "pyodide") return { target: "browser" };
  if (first === "worker") return { target: "worker", explicit: true };
  return {
    target: "denied",
    capability: capability ?? undefined,
    reason:
      "No execution engine is allowed for this plugin job. Ask an administrator to change the execution policy in the Admin Panel (Execution).",
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
  /** The execution policy's order (default: resolved for this plugin id). */
  order?: readonly ExecEngine[];
  store?: LocalArtefactStore;
  onLog?: (msg: string) => void;
  onProgress?: (message: string, fraction: number) => void;
  /** Injectable for tests: the in-browser run and the worker submission. */
  runInBrowser?: typeof runPluginJobViaPyodide;
  submitToWorker?: typeof pluginsApi.pluginJob;
}): Promise<DispatchPluginJobOutcome> {
  const wasmCapable = await isPluginWasmCapable(params.pluginId);
  const order =
    params.order ??
    engineOrder("plugin_job", params.pluginId, {
      capability: params.capability ?? null,
      workerAccess: params.workerAccess ?? null,
    });
  const decision = decideExecutionTarget({
    wasmCapable,
    workerAccess: params.workerAccess,
    capability: params.capability,
    preferWorker: params.preferWorker,
    order,
  });

  if (decision.target === "denied") {
    throw new Error(decision.reason);
  }

  const toWorker = async (explicit: boolean): Promise<WorkerPluginJobOutcome> => {
    const submit = params.submitToWorker ?? pluginsApi.pluginJob;
    const queued = await submit(
      params.pluginId,
      {
        options: params.options,
        derived_key: params.derivedKey,
        derived_prefix: params.derivedPrefix,
        capability: params.capability,
      },
      { scope: params.scope, runOnWorker: explicit },
    );
    return { execution: "worker", ...queued };
  };

  if (decision.target === "worker") {
    return toWorker(decision.explicit === true);
  }

  const token = getAccessToken();
  const authHeaders = token ? { Authorization: `Bearer ${token}` } : undefined;
  const runBrowser = params.runInBrowser ?? runPluginJobViaPyodide;
  let res: Awaited<ReturnType<typeof runPluginJobViaPyodide>>;
  try {
    res = await runBrowser({
      pluginId: params.pluginId,
      scope: params.scope,
      options: params.options,
      derivedPrefix: params.derivedPrefix,
      apiBase: runtime.apiBase(),
      authHeaders,
      onLog: params.onLog,
      onProgress: params.onProgress,
    });
  } catch (err) {
    // The policy's next engine after the browser, when it lists the worker (and
    // the user may use it): an explicit worker choice, judged by the server.
    const next = order.slice(order.indexOf("pyodide") + 1);
    if ((err as { name?: string })?.name === "AbortError" || !next.includes("worker")) throw err;
    if (!canUseWorker(params.workerAccess, params.capability)) throw err;
    const reason = err instanceof Error ? err.message : String(err);
    (params.onLog ?? ((m: string) => console.info(m)))(
      `[execution] plugin_job ${params.pluginId}: Pyodide failed (${reason}); trying Server worker`,
    );
    return toWorker(true);
  }

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
