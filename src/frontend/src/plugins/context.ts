// Assembles the concrete runtime `AdaPluginContext` from the viewer's stores,
// runtime and REST config. This is the ONE place that bridges the
// dependency-free registry (`@/plugins/registry`) to the heavy parts (the
// mounted viewer's scene handles, the zustand stores, the REST config) — kept
// out of the registry so the registry stays unit-testable under plain node.
//
// The `SceneHandle` a plugin gets resolves `getViewerRuntime()` on every call
// rather than closing over one scene, so a handle built at plugin-load time
// still points at the scene that is actually mounted. The plugin-facing shape
// is unchanged.

import { trackJob } from "@/services/jobTracking";
import { runtime } from "@/runtime/config";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { requestRender } from "@/state/perfStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useColorStore } from "@/state/colorLegendStore";
import { noteOwnerPainted } from "@/utils/scene/fea/modeSceneColor";
import { effectivePluginTheme, useThemeStore } from "@/state/themeStore";
import type { PluginTheme } from "./registry";
import {
  getActiveFeaMesh,
  getActiveFeaSelectedRangeIds,
  setActiveFeaSelectedRangeIds,
} from "@/utils/scene/handlers/load_fea_streaming";
import { useModelState } from "@/state/modelState";
import { getSingletonViewerStores, type AdaViewerStores } from "@/state/AdaViewerContext";
import type {
  AdaPluginContext,
  PluginApiClient,
  PluginLogLevel,
  SceneColorFieldResult,
  SceneHandle,
} from "./registry";

function makeApiClient(): PluginApiClient {
  const base = runtime.apiBase();
  return {
    base,
    plugin: (id?: string) => `${base}/plugins/${id ?? ""}`.replace(/\/$/, ""),
  };
}

// A plugin-owned Object3D is tagged with its owner id so core helpers
// (zoomToAll's helper-exclusion, isolate, model-clear) can treat plugin
// resources uniformly. Kept structural to avoid importing `three` here.
type Object3DLike = {
  userData?: Record<string, unknown>;
  parent?: { remove: (o: unknown) => void } | null;
};

function makeSceneHandle(): SceneHandle {
  return {
    add(owner, obj) {
      const scene = getViewerRuntime().scene.current as unknown as {
        add: (o: unknown) => void;
      } | null;
      const o = obj as Object3DLike;
      o.userData = { ...(o.userData ?? {}), __pluginOwner: owner };
      scene?.add(obj);
      requestRender();
    },
    remove(owner, obj) {
      const scene = getViewerRuntime().scene.current as unknown as {
        remove: (o: unknown) => void;
        children?: Object3DLike[];
      } | null;
      if (!scene) return;
      if (obj) {
        scene.remove(obj);
      } else {
        // Remove every top-level object this owner added.
        for (const child of [...(scene.children ?? [])]) {
          if (child.userData?.__pluginOwner === owner) scene.remove(child);
        }
      }
      requestRender();
    },
    requestRender: () => requestRender(),
    paintField(fieldId: string, result: SceneColorFieldResult) {
      // Core owns the single active-field arbiter: drive the shared color legend
      // off the provider's range. (A provider that ships per-entity `values`
      // additionally feeds the FEA applyField paint path — wired in Phase 2 when
      // the first real color-field plugin lands; the scaffold proves the legend
      // seam.)
      const legend = useColorStore.getState();
      const [lo, hi] = result.range;
      legend.setMin(lo);
      legend.setMax(hi);
      legend.setShowLegend(true);
      // A legend-only painter never touches the field buffers, so the mode
      // arbiter would otherwise see a mode that painted nothing and suspend it
      // again on re-entry instead of putting its legend back.
      noteOwnerPainted();
      requestRender();
      void fieldId;
    },
    getActiveFeaMesh: () => getActiveFeaMesh(),
    getSelectedFeaRangeIds: () => getActiveFeaSelectedRangeIds(),
    setSelectedFeaRanges: (rangeIds, additive) => setActiveFeaSelectedRangeIds(rangeIds, additive),
    async loadModelFromUrl(owner, url, opts) {
      // Dynamic import for the same reason overlay_file_in_scene is dynamically
      // imported at its call sites: the loader pulls in three + GLTFLoader +
      // the meshopt decoder, and this module is on the boot path.
      const { setupModelLoaderAsync } = await import(
        "@/components/viewer/sceneHelpers/setupModelLoader"
      );
      const sourceName =
        opts?.sourceName || url.split("?")[0].split("/").pop() || `${owner}-model`;
      const group = await setupModelLoaderAsync({
        modelUrl: url,
        translate: opts?.translate ?? true,
        sourceName,
        requestHeaders: opts?.headers,
        sourceUpAxis: opts?.sourceUpAxis ?? "z",
      });
      // Register the source -> group mapping so the model shows up in the
      // loaded-sources list and `unloadModel` can drop just this one, exactly as
      // core's own overlay path does.
      useModelState.getState().registerLoadedSource(sourceName, group);
      requestRender();
    },
    unloadModel(sourceName) {
      void import("@/utils/scene/handlers/unload_source_from_scene").then(
        ({ unload_source_from_scene }) => unload_source_from_scene(sourceName),
      );
    },
  };
}

/** Build a plugin context bound to a specific plugin id. Stores are the live
 * viewer singletons (via the provider's shape); scope + api are read lazily so
 * the context stays valid across scope switches without rebuilding. */
export function makePluginContext(
  pluginId: string,
  stores: AdaViewerStores,
  theme?: PluginTheme,
): AdaPluginContext {
  const log = (level: PluginLogLevel, msg: string, ...args: unknown[]) => {
    const line = `[plugin:${pluginId}] ${msg}`;
    if (level === "error") console.error(line, ...args);
    else if (level === "warn") console.warn(line, ...args);
    else if (level === "info") console.info(line, ...args);
    else console.debug(line, ...args);
  };
  return {
    pluginId,
    api: makeApiClient(),
    stores,
    scene: makeSceneHandle(),
    scope: () => scopeUrlPart(useScopeStore.getState().current),
    // A React caller passes the SUBSCRIBED theme (`usePluginTheme`), which is
    // what makes a plugin panel repaint on a theme switch. The fallback is a
    // snapshot, for the callers that have no React tree to subscribe in — the
    // sidecar-loader run-point and url-param dispatch below.
    //
    // This used to be the snapshot unconditionally, justified by "the slot host
    // re-renders on a theme switch". It does not: the hosts subscribed to the
    // viewer stores and the plugin-visibility store, never to the theme, so a
    // panel kept whatever tokens were current at its last unrelated render.
    theme: theme ?? effectivePluginTheme(useThemeStore.getState()),
    log,
    // The scope is resolved HERE rather than asked of the plugin: a job belongs to
    // the scope it was enqueued in, and letting a caller pass a different one would
    // make the toast poll a job the API will not authorise it for.
    trackJob: (opts) =>
      trackJob({...opts, scopeUrl: scopeUrlPart(useScopeStore.getState().current)}),
  };
}

/** Build a plugin context outside the React tree (for the model-load
 * sidecar-loader run-point and url-param dispatch). Uses the Phase-1 singleton
 * store bag so it sees the same store instances React consumers do. */
export function makePluginContextStandalone(pluginId: string): AdaPluginContext {
  return makePluginContext(pluginId, getSingletonViewerStores());
}
