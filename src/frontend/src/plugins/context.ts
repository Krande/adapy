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
import { usePluginColorScaleStore } from "@/state/pluginColorScaleStore";
import { noteOwnerPainted } from "@/utils/scene/fea/modeSceneColor";
import { effectivePluginTheme, useThemeStore } from "@/state/themeStore";
import type { PluginTheme } from "./registry";
import {
  addElementOutlines,
  addLabels,
  addLines,
  frameAll,
  frameFeaElements,
  frameSceneBox,
  hasFeaModel,
  hiddenFeaElements,
  hideFeaElements,
  isolateFeaElements,
  paintFeaElements,
  removeOwnerOverlays,
  setFeaElementEdgesVisible,
} from "./sceneOverlays";
import {
  addImage,
  addLineLayer,
  addPointLayer,
  imageUnavailable,
  makePluginLayers,
  makePluginPlayback,
  poseModel,
} from "./sceneLayers";
import { makePluginSimulations } from "./pluginSimulations";
import { onSceneCleared } from "./sceneCleared";
import { endScenePicks, pickObjects } from "./scenePicks";
import { endSceneHighlights, highlightObjects } from "./sceneHighlights";
import { placeBox } from "./sceneBox";
import { contactBetween } from "./sceneContact";
import { findObjectsByName } from "@/utils/tree_view/modelSource";
import { releaseFeaDeformation, setFeaDeformationShown } from "@/utils/scene/fea/deformationGate";
import { releaseFeaUnpaintedColor, setFeaUnpaintedColor } from "@/utils/scene/fea/unpaintedColor";
import { findFaces, focusFace } from "@/utils/mesh_select/faceSearch";
import {
  getActiveFeaMesh,
  getActiveFeaSelectedRangeIds,
  setActiveFeaSelectedRangeIds,
} from "@/utils/scene/handlers/load_fea_streaming";
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
      if (obj) {
        scene?.remove(obj);
      } else {
        // Remove every top-level object this owner added, and its overlays. The overlays are
        // drawn by whichever viewport is on screen: there need be no three.js scene for them.
        for (const child of [...(scene?.children ?? [])]) {
          if (child.userData?.__pluginOwner === owner) scene!.remove(child);
        }
        removeOwnerOverlays(owner);
        endScenePicks(owner);
        endSceneHighlights(owner);
        releaseFeaDeformation(owner);
        releaseFeaUnpaintedColor(owner);
        usePluginColorScaleStore.getState().show(owner, null);
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
      // ONE PATH FOR BOTH RENDERERS. This built a three.js scene itself, so with the three.js-free
      // viewport on screen it logged "Scene reference is null", added nothing, and left the source
      // registered as loaded. It now goes through core's own loader (`loadModel` below uses the
      // same), which hands the URL -- and the caller's headers -- to whichever viewport is on
      // screen, registers the source, and remembers the load for a switch of viewport.
      // Imported on use: the loader pulls in three + GLTFLoader, and this module is on the boot path.
      const { loadModel } = await import("@/components/viewer/sceneHelpers/loadModel");
      const sourceName =
        opts?.sourceName || url.split("?")[0].split("/").pop() || `${owner}-model`;
      await loadModel({
        sourceName,
        displayName: opts?.displayName,
        bytes: { from: "url", url, headers: opts?.headers },
        // The model joins what is in the scene, as it always did here.
        placement: "overlay",
        translate: opts?.translate ?? true,
        sourceUpAxis: opts?.sourceUpAxis ?? "z",
        // The same load profiling the viewer's own file loads get (admin, opt-in).
        metrics: true,
      });
      requestRender();
    },
    async loadModel(owner, source) {
      // Core's own loader, which hands the bytes to whichever viewport is on screen. Imported on
      // use, as `loadModelFromUrl` imports its loader: this module is on the boot path.
      const { loadModel } = await import("@/components/viewer/sceneHelpers/loadModel");
      const { bytes, url } = source;
      if (!bytes && !url) throw new Error(`[plugin:${owner}] loadModel needs bytes or a url`);
      await loadModel({
        sourceName: source.sourceName,
        displayName: source.displayName,
        bytes: bytes
          ? { from: "bytes", bytes: bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes) }
          : { from: "url", url: url! },
        placement: source.placement ?? "replace",
        sourceUpAxis: source.sourceUpAxis,
        posable: source.posable,
        autoFitOverride: source.keepCamera ? false : undefined,
      });
      requestRender();
    },
    poseModel,
    unloadModel(sourceName) {
      void import("@/utils/scene/handlers/unload_any_source").then(({ unload_any_source }) => unload_any_source(sourceName));
    },
    async clearScene() {
      // The Scene tab's "Remove all": queued loads, models, an open procedural model, what core
      // drew over them, and every plugin's overlays.
      const { clearScene } = await import("@/utils/scene/handlers/clear_scene");
      await clearScene({ reason: "plugin" });
      requestRender();
    },
    onSceneCleared,
    addLines,
    addLineLayer,
    addPointLayer,
    addImage,
    imageUnavailable,
    addLabels,
    hasFeaModel,
    paintFeaElements,
    addElementOutlines,
    isolateFeaElements,
    hideFeaElements,
    hiddenFeaElements,
    setFeaElementEdgesVisible,
    showColorScale(owner, scale) {
      usePluginColorScaleStore.getState().show(owner, scale);
      if (scale) useColorStore.getState().setShowLegend(true);
      requestRender();
    },
    setFeaDeformationShown,
    setFeaUnpaintedColor,
    frameFeaElements,
    frameBox: frameSceneBox,
    frameAll,
    findFaces,
    focusFace: (face, opts) => focusFace(face, { frame: opts?.frame }),
    pickObjects,
    placeBox,
    contactBetween,
    highlightObjects,
    findObjects: (names, options) => findObjectsByName(names, options),
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
    playback: makePluginPlayback(pluginId),
    layers: makePluginLayers(pluginId),
    simulations: makePluginSimulations(pluginId, () => scopeUrlPart(useScopeStore.getState().current)),
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
