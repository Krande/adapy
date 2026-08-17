// Model-load extension seam: after core geometry (CAD or FEA) is visible, offer
// the loaded manifest to every registered result-sidecar loader. A loader that
// `detect`s its data runs and returns a disposer; core holds the disposers and
// tears them down on the next model load / clear. This generalises the existing
// single-purpose `SetupModelPrepareHook` into the multi-plugin run-point.

import { makePluginContextStandalone } from "./context";
import { getResultSidecarLoaders, disablePlugin, type SidecarFetcher } from "./registry";

// loader id -> disposer for the currently-loaded model.
const _active = new Map<string, () => void>();

/** Default fetcher: resolves sidecar keys relative to a base URL. Plugins fetch
 * their own `{sidecarPrefix}.*` blobs through this without core knowing shapes. */
export function makeManifestFetcher(baseUrl: string, headers?: Record<string, string>): SidecarFetcher {
  const resolve = (relKey: string) => new URL(relKey, baseUrl).toString();
  return {
    url: resolve,
    json: async (relKey) => {
      const r = await fetch(resolve(relKey), { headers });
      if (!r.ok) throw new Error(`sidecar ${relKey}: HTTP ${r.status}`);
      return r.json();
    },
    bytes: async (relKey, range) => {
      const h = { ...(headers ?? {}) } as Record<string, string>;
      if (range) h["Range"] = `bytes=${range.start}-${range.end}`;
      const r = await fetch(resolve(relKey), { headers: h });
      if (!r.ok && r.status !== 206) throw new Error(`sidecar ${relKey}: HTTP ${r.status}`);
      return r.arrayBuffer();
    },
  };
}

/** Tear down every active sidecar loader (call on model clear / before a new
 * load). Each disposer is isolated so one throwing disposer can't block others. */
export function disposeResultSidecarLoaders(): void {
  for (const [id, dispose] of _active) {
    try {
      dispose();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn(`[plugins] sidecar disposer for ${id} threw`, err);
    }
  }
  _active.clear();
}

/** Run all registered sidecar loaders against a freshly-loaded model. Disposes
 * any previously-active loaders first. Every detect/load is try/caught: a
 * throwing plugin is disabled for the session and never crashes the load. */
export async function runResultSidecarLoaders(args: {
  manifest: unknown;
  fetcher: SidecarFetcher;
  scope: string;
  sourceName?: string;
}): Promise<void> {
  disposeResultSidecarLoaders();
  // A throwaway ctx just to enumerate active loaders; each loader gets its own
  // plugin-scoped ctx below.
  const enumCtx = makePluginContextStandalone("");
  for (const { pluginId, loader } of getResultSidecarLoaders(enumCtx)) {
    const ctx = makePluginContextStandalone(pluginId);
    try {
      if (!loader.detect(args.manifest)) continue;
      const dispose = await loader.load(ctx, args);
      if (typeof dispose === "function") _active.set(loader.id, dispose);
    } catch (err) {
      disablePlugin(pluginId, `sidecar loader "${loader.id}" threw: ${String(err)}`);
    }
  }
}

/** Number of currently-active loaders — test/introspection helper. */
export function activeSidecarLoaderCount(): number {
  return _active.size;
}
