// Model-load extension seam: after core geometry (CAD or FEA) is visible, offer
// the loaded manifest to every registered result-sidecar loader. A loader that
// `detect`s its data runs and returns a disposer. The disposers belong to the model
// they loaded for (its source name): loading another model beside it -- a design GLB
// over a SIN's results -- runs that model's loaders alongside and leaves these be;
// they are torn down when their model is loaded again, leaves the scene, or the scene
// is cleared. This generalises the existing single-purpose `SetupModelPrepareHook`
// into the multi-plugin run-point.

import { useModelState } from "@/state/modelState";
import { makePluginContextStandalone } from "./context";
import { getResultSidecarLoaders, disablePlugin, notifyPluginActivationChanged, type SidecarFetcher } from "./registry";
import { onSceneCleared } from "./sceneCleared";

/** The source a load named none for (a follower page's, a test's). */
const NO_SOURCE = "";

// source name -> (loader id -> disposer) for the models in the scene.
const _active = new Map<string, Map<string, () => void>>();
/** Sources seen among the loaded models: only those can be seen leaving. */
const _seenLoaded = new Set<string>();
let _watching = false;

/** Tear the loaders of a model down when it leaves the scene, and all of them on a clear. */
function watchScene(): void {
  if (_watching) return;
  _watching = true;
  onSceneCleared(() => disposeResultSidecarLoaders());
  useModelState.subscribe((s, prev) => {
    if (s.loadedSourceNames === prev.loadedSourceNames) return;
    for (const name of s.loadedSourceNames) _seenLoaded.add(name);
    for (const name of [..._active.keys()]) {
      if (_seenLoaded.has(name) && !s.loadedSourceNames.has(name)) {
        _seenLoaded.delete(name);
        disposeResultSidecarLoaders(name);
      }
    }
  });
}

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

/** Tear down the active sidecar loaders of `sourceName`, or of every model when it is left out (a
 * clear). Each disposer is isolated so one throwing disposer can't block others. */
export function disposeResultSidecarLoaders(sourceName?: string): void {
  const sources = sourceName === undefined ? [..._active.keys()] : [sourceName];
  for (const source of sources) {
    const loaders = _active.get(source);
    if (!loaders) continue;
    _active.delete(source);
    for (const [id, dispose] of loaders) {
      try {
        dispose();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(`[plugins] sidecar disposer for ${id} threw`, err);
      }
    }
  }
  // What the loaders loaded is gone: a panel whose predicate read it asks again.
  notifyPluginActivationChanged();
}

/** Run all registered sidecar loaders against a freshly-loaded model. Disposes the
 * loaders that model had (a reload), not another model's. Every detect/load is
 * try/caught: a throwing plugin is disabled for the session and never crashes the load. */
export async function runResultSidecarLoaders(args: {
  manifest: unknown;
  fetcher: SidecarFetcher;
  scope: string;
  sourceName?: string;
}): Promise<void> {
  watchScene();
  const source = args.sourceName ?? NO_SOURCE;
  disposeResultSidecarLoaders(source);
  // A throwaway ctx just to enumerate active loaders; each loader gets its own
  // plugin-scoped ctx below.
  const enumCtx = makePluginContextStandalone("");
  for (const { pluginId, loader } of getResultSidecarLoaders(enumCtx)) {
    const ctx = makePluginContextStandalone(pluginId);
    try {
      if (!loader.detect(args.manifest)) continue;
      const dispose = await loader.load(ctx, args);
      if (typeof dispose === "function") {
        let loaders = _active.get(source);
        if (!loaders) _active.set(source, (loaders = new Map()));
        loaders.set(loader.id, dispose);
      }
      // A plugin's activation predicate often reads what its loader just loaded (its
      // results). The panel asked before the load finished - on the model change that
      // started it - and would otherwise show "Nothing to show here yet" until some
      // unrelated change made it ask again (a mode switch, a reload).
      notifyPluginActivationChanged();
    } catch (err) {
      disablePlugin(pluginId, `sidecar loader "${loader.id}" threw: ${String(err)}`);
    }
  }
}

/** Number of currently-active loaders — test/introspection helper. */
export function activeSidecarLoaderCount(): number {
  let n = 0;
  for (const loaders of _active.values()) n += loaders.size;
  return n;
}
