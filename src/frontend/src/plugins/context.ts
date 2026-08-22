// Assembles the concrete runtime `AdaPluginContext` from the viewer's stores,
// refs, and REST config. This is the ONE place that bridges the dependency-free
// registry (`@/plugins/registry`) to the heavy singletons (three scene refs,
// zustand stores, runtime config) — kept out of the registry so the registry
// stays unit-testable under plain node.

import { runtime } from "@/runtime/config";
import { sceneRef } from "@/state/refs";
import { requestRender } from "@/state/perfStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useColorStore } from "@/state/colorLegendStore";
import { effectivePluginTheme, useThemeStore } from "@/state/themeStore";
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
      const scene = sceneRef.current as unknown as {
        add: (o: unknown) => void;
      } | null;
      const o = obj as Object3DLike;
      o.userData = { ...(o.userData ?? {}), __pluginOwner: owner };
      scene?.add(obj);
      requestRender();
    },
    remove(owner, obj) {
      const scene = sceneRef.current as unknown as {
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
      const group = await setupModelLoaderAsync(
        url,
        opts?.translate ?? true,
        undefined,
        sourceName,
        opts?.headers,
      );
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
export function makePluginContext(pluginId: string, stores: AdaViewerStores): AdaPluginContext {
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
    // Snapshot the active theme tokens. makePluginContext is rebuilt on every
    // slot-host render, so a theme switch (which re-renders subscribers) re-reads
    // the current values; plugin panels that also want live updates can read the
    // `--ada-*` CSS vars this mirrors.
    theme: effectivePluginTheme(useThemeStore.getState()),
    log,
  };
}

/** Build a plugin context outside the React tree (for the model-load
 * sidecar-loader run-point and url-param dispatch). Uses the Phase-1 singleton
 * store bag so it sees the same store instances React consumers do. */
export function makePluginContextStandalone(pluginId: string): AdaPluginContext {
  return makePluginContext(pluginId, getSingletonViewerStores());
}
