// The per-instance state of one mounted viewer: its scene-graph handles and
// its zustand stores, reached through one React context.
//
// The viewer used to keep both as module-level singletons — ten `createRef()`s
// in `state/refs.ts` and a `create()` hook per `state/*Store.ts` — which is one
// viewer's worth of state for the whole JS realm. Fine for a full-page app, and
// wrong the moment a page wants two: a docs page embedding N mode shapes, a
// side-by-side comparison, a follower window. The second viewer to mount
// overwrote the first one's scene, camera and renderer, and every consumer then
// drew into, picked against and tore down the survivor.
//
// WHERE THAT LANDED
// -----------------
// The scene-graph half is done. `AdaViewerProvider` creates a `ViewerRuntime`
// (`./viewerRuntime`) per mount; components read it with `useViewerRefs()`, and
// the imperative modules that have no tree to read a context from — scene
// utilities, click handlers, the websocket handlers, the model loader — reach
// the mounted one with `getViewerRuntime()`, the same shape the equally
// imperative load paths use to reach the open model
// (`useModelSessionStore.getState().current()`). `state/refs.ts` is gone, and
// `src/__tests__/state/viewerRuntime.multiInstance.test.tsx` holds the property
// that cost: two providers, two runtimes, neither able to see the other's
// scene.
//
// The store half is not, and the remaining step is not a migration but a
// rewrite of the stores themselves. `SINGLETON_VIEWER_STORES` below names every
// store the provider hands out, so a consumer that reads through
// `useViewerStores()` needs no edit when a store becomes per-instance — but
// each one is still a `create()` singleton, and making it otherwise means
// `createStore()` + `useStore(api, selector)` inside the provider, one store at
// a time. Until that happens two providers share store state while having
// genuinely separate scenes, which is the honest description of where this is:
// the 3D collision is fixed, the state collision is not.
//
// Anything new should read the context (or `getViewerRuntime()`), never a
// store module's export directly.

import React, { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react"

import {
    createViewerRuntime,
    mountViewerRuntime,
    type ViewerRuntime,
} from "./viewerRuntime"

import { useAnimationStore as g_useAnimationStore } from "./animationStore"
import { useAuditFilterStore as g_useAuditFilterStore } from "./auditFilterStore"
import { useAuditToastStore as g_useAuditToastStore } from "./auditToastStore"
import { useCellBuilderStore as g_useCellBuilderStore } from "./cellBuilderStore"
import { useColorStore as g_useColorStore } from "./colorLegendStore"
import { useCompanionModelStore as g_useCompanionModelStore } from "./companionModelStore"
import { useComponentBuildStore as g_useComponentBuildStore } from "./componentBuildStore"
import { useComponentControlsStore as g_useComponentControlsStore } from "./componentControlsStore"
import { useComponentSpecsStore as g_useComponentSpecsStore } from "./componentSpecsStore"
import { useCompressionStore as g_useCompressionStore } from "./compressionStore"
import { useComputeStore as g_useComputeStore } from "./computeStore"
import { useConnectionGraphStore as g_useConnectionGraphStore } from "./connectionGraphStore"
import { useConversionStore as g_useConversionStore } from "./conversionStore"
import { useConvertPageStore as g_useConvertPageStore } from "./convertPageStore"
import { useEngineCatalogStore as g_useEngineCatalogStore } from "./engineCatalogStore"
import { useEquipmentCatalogStore as g_useEquipmentCatalogStore } from "./equipmentCatalogStore"
import { useExperimentalStore as g_useExperimentalStore } from "./experimentalStore"
import { useExternalModelsStore as g_useExternalModelsStore } from "./externalModelsStore"
import { useFeaAnimationStore as g_useFeaAnimationStore } from "./feaAnimationStore"
import { useFemConceptsStore as g_useFemConceptsStore } from "./femConceptsStore"
import { useGalleryStore as g_useGalleryStore } from "./galleryStore"
import { useLineageStore as g_useLineageStore } from "./lineageStore"
import { useMeStore as g_useMeStore } from "./meStore"
import { useMeshPanelStore as g_useMeshPanelStore } from "./meshPanelStore"
import { useModelSessionStore as g_useModelSessionStore } from "./modelSession"
import { useModelState as g_useModelState } from "./modelState"
import { useNodeEditorStore as g_useNodeEditorStore } from "./useNodeEditorStore"
import { useObjectInfoStore as g_useObjectInfoStore } from "./objectInfoStore"
import { useOptionsStore as g_useOptionsStore } from "./optionsStore"
import { usePerfStore as g_usePerfStore } from "./perfStore"
import { useSceneColorOwnerStore as g_useSceneColorOwnerStore } from "./sceneColorOwnerStore"
import { useSceneInfoStore as g_useSceneInfoStore } from "./sceneInfoStore"
import { useScopeStore as g_useScopeStore } from "./scopeStore"
import { useSectionStore as g_useSectionStore } from "./sectionStore"
import { useSelectedObjectStore as g_useSelectedObjectStore } from "./useSelectedObjectStore"
import { useServerInfoStore as g_useServerInfoStore } from "./serverInfoStore"
import { useStatsStore as g_useStatsStore } from "./statsStore"
import { useTableNavStore as g_useTableNavStore } from "./tableNavStore"
import { useTreeViewStore as g_useTreeViewStore } from "./treeViewStore"
import { useTypeIconsStore as g_useTypeIconsStore } from "./typeIconsStore"
import { useViewMetricsStore as g_useViewMetricsStore } from "./viewMetricsStore"
import { useViewerPanelStore as g_useViewerPanelStore } from "./viewerPanelStore"
import { useWebSocketStore as g_useWebSocketStore } from "./webSocketStore"
import { useWebsocketStatusStore as g_useWebsocketStatusStore } from "./websocketStatusStore"


/**
 * One viewer instance's scene-graph handles — the shape `useViewerRefs()`
 * returns. Defined by `ViewerRuntime` in `./viewerRuntime`, which is also what
 * the imperative (non-React) modules read through `getViewerRuntime()`; the
 * alias is kept because `@/viewer-core/app` publishes this name to out-of-tree
 * UI shells.
 */
export type AdaViewerRefs = ViewerRuntime

/**
 * Every zustand store the provider hands out, indexed by its own export name
 * (`useModelState`, not `useModel`) so a consumer migrating off a direct import
 * does not have to rename call sites.
 *
 * 44 of the viewer's 46 stores are here. The two that are not, and why:
 *
 *   `useThemeStore`      A process singleton by intent. The theme is persisted
 *                        per browser and applied to `document.documentElement`,
 *                        so two viewers on one page cannot disagree about it
 *                        without one of them being wrong. `usePluginTheme()`
 *                        reads it directly for the same reason.
 *   `useLoadQueueStore`  Dependency weight, not semantics. It statically
 *                        imports the FEA streaming loader, which reaches 44
 *                        modules under `utils/scene` and a Vite-only
 *                        `?worker&inline` import — so naming it here would put
 *                        the whole 3D chain on this module's graph, and this
 *                        module is imported by `app.tsx`, including on the
 *                        canvas-less `/convert` and `/admin` routes that exist
 *                        to avoid exactly that. It would also stop the context
 *                        importing under plain node, which is what the
 *                        multi-instance test needs. A dynamic import inside the
 *                        store would settle it.
 *
 * `state/adminPanelStore.ts` is absent because it holds types only — there is
 * no store in it.
 */
export interface AdaViewerStores {
    useAnimationStore: typeof g_useAnimationStore
    useAuditFilterStore: typeof g_useAuditFilterStore
    useAuditToastStore: typeof g_useAuditToastStore
    useCellBuilderStore: typeof g_useCellBuilderStore
    useColorStore: typeof g_useColorStore
    useCompanionModelStore: typeof g_useCompanionModelStore
    useComponentBuildStore: typeof g_useComponentBuildStore
    useComponentControlsStore: typeof g_useComponentControlsStore
    useComponentSpecsStore: typeof g_useComponentSpecsStore
    useCompressionStore: typeof g_useCompressionStore
    useComputeStore: typeof g_useComputeStore
    useConnectionGraphStore: typeof g_useConnectionGraphStore
    useConversionStore: typeof g_useConversionStore
    useConvertPageStore: typeof g_useConvertPageStore
    useEngineCatalogStore: typeof g_useEngineCatalogStore
    useEquipmentCatalogStore: typeof g_useEquipmentCatalogStore
    useExperimentalStore: typeof g_useExperimentalStore
    useExternalModelsStore: typeof g_useExternalModelsStore
    useFeaAnimationStore: typeof g_useFeaAnimationStore
    useFemConceptsStore: typeof g_useFemConceptsStore
    useGalleryStore: typeof g_useGalleryStore
    useLineageStore: typeof g_useLineageStore
    useMeStore: typeof g_useMeStore
    useMeshPanelStore: typeof g_useMeshPanelStore
    useModelSessionStore: typeof g_useModelSessionStore
    useModelState: typeof g_useModelState
    useNodeEditorStore: typeof g_useNodeEditorStore
    useObjectInfoStore: typeof g_useObjectInfoStore
    useOptionsStore: typeof g_useOptionsStore
    usePerfStore: typeof g_usePerfStore
    useSceneColorOwnerStore: typeof g_useSceneColorOwnerStore
    useSceneInfoStore: typeof g_useSceneInfoStore
    useScopeStore: typeof g_useScopeStore
    useSectionStore: typeof g_useSectionStore
    useSelectedObjectStore: typeof g_useSelectedObjectStore
    useServerInfoStore: typeof g_useServerInfoStore
    useStatsStore: typeof g_useStatsStore
    useTableNavStore: typeof g_useTableNavStore
    useTreeViewStore: typeof g_useTreeViewStore
    useTypeIconsStore: typeof g_useTypeIconsStore
    useViewMetricsStore: typeof g_useViewMetricsStore
    useViewerPanelStore: typeof g_useViewerPanelStore
    useWebSocketStore: typeof g_useWebSocketStore
    useWebsocketStatusStore: typeof g_useWebsocketStatusStore
}

export interface AdaViewerCtx {
    refs: AdaViewerRefs
    stores: AdaViewerStores
}

const AdaViewerContext = createContext<AdaViewerCtx | null>(null)


/**
 * The store bag, built once. The provider hands it to React consumers via
 * `useViewerStores()`; non-React callers (the plugin sidecar-loader run-point,
 * which fires mid model-load outside the React tree) read it through
 * `getSingletonViewerStores()`. Both paths therefore see the same store
 * instances.
 *
 * Still `SINGLETON_` because the stores still are: see the note on
 * `AdaViewerStores` for what turning them per-instance would take.
 */
export const SINGLETON_VIEWER_STORES: AdaViewerStores = {
    useAnimationStore: g_useAnimationStore,
    useAuditFilterStore: g_useAuditFilterStore,
    useAuditToastStore: g_useAuditToastStore,
    useCellBuilderStore: g_useCellBuilderStore,
    useColorStore: g_useColorStore,
    useCompanionModelStore: g_useCompanionModelStore,
    useComponentBuildStore: g_useComponentBuildStore,
    useComponentControlsStore: g_useComponentControlsStore,
    useComponentSpecsStore: g_useComponentSpecsStore,
    useCompressionStore: g_useCompressionStore,
    useComputeStore: g_useComputeStore,
    useConnectionGraphStore: g_useConnectionGraphStore,
    useConversionStore: g_useConversionStore,
    useConvertPageStore: g_useConvertPageStore,
    useEngineCatalogStore: g_useEngineCatalogStore,
    useEquipmentCatalogStore: g_useEquipmentCatalogStore,
    useExperimentalStore: g_useExperimentalStore,
    useExternalModelsStore: g_useExternalModelsStore,
    useFeaAnimationStore: g_useFeaAnimationStore,
    useFemConceptsStore: g_useFemConceptsStore,
    useGalleryStore: g_useGalleryStore,
    useLineageStore: g_useLineageStore,
    useMeStore: g_useMeStore,
    useMeshPanelStore: g_useMeshPanelStore,
    useModelSessionStore: g_useModelSessionStore,
    useModelState: g_useModelState,
    useNodeEditorStore: g_useNodeEditorStore,
    useObjectInfoStore: g_useObjectInfoStore,
    useOptionsStore: g_useOptionsStore,
    usePerfStore: g_usePerfStore,
    useSceneColorOwnerStore: g_useSceneColorOwnerStore,
    useSceneInfoStore: g_useSceneInfoStore,
    useScopeStore: g_useScopeStore,
    useSectionStore: g_useSectionStore,
    useSelectedObjectStore: g_useSelectedObjectStore,
    useServerInfoStore: g_useServerInfoStore,
    useStatsStore: g_useStatsStore,
    useTableNavStore: g_useTableNavStore,
    useTreeViewStore: g_useTreeViewStore,
    useTypeIconsStore: g_useTypeIconsStore,
    useViewMetricsStore: g_useViewMetricsStore,
    useViewerPanelStore: g_useViewerPanelStore,
    useWebSocketStore: g_useWebSocketStore,
    useWebsocketStatusStore: g_useWebsocketStatusStore,
}

/** Non-hook accessor for the store bag (see above). */
export function getSingletonViewerStores(): AdaViewerStores {
    return SINGLETON_VIEWER_STORES
}


/**
 * Read the surrounding `<AdaViewerProvider>`'s context. Throws when
 * called outside one — every consumer is supposed to live inside an
 * `<AdaViewerProvider>`, so a missing provider is a programmer
 * error, not something to silently null-fallback.
 */
export function useAdaViewerCtx(): AdaViewerCtx {
    const ctx = useContext(AdaViewerContext)
    if (ctx == null) {
        throw new Error(
            "useAdaViewerCtx() must be called inside an <AdaViewerProvider>. " +
            "Wrap your component tree with <AdaViewerProvider> (the standalone " +
            "viewer app does this at the root in app.tsx).",
        )
    }
    return ctx
}

/**
 * This viewer instance's scene-graph handles. The imperative counterpart, for
 * modules with no React tree, is `getViewerRuntime()`.
 */
export function useViewerRefs(): AdaViewerRefs {
    return useAdaViewerCtx().refs
}

/** Per-instance zustand hooks (today a thin pass-through to the singletons). */
export function useViewerStores(): AdaViewerStores {
    return useAdaViewerCtx().stores
}


/**
 * Mounts one viewer's state. Gives its children a `ViewerRuntime` of their own
 * — created here, registered as the mounted one for imperative callers, and
 * dropped on unmount — plus the store bag.
 *
 * Every consumer of viewer state is expected to be under one: `useViewerRefs()`
 * and `useViewerStores()` throw outside it rather than silently falling back,
 * because a component reading a scene that belongs to nobody is a bug that
 * would otherwise present as an empty canvas.
 */
export function AdaViewerProvider({ children }: { children: ReactNode }) {
    // One runtime per mount, created on the first render and kept for the life
    // of it. `useRef` rather than `useMemo` because this is identity, not a
    // cached computation: a recomputed `useMemo` would hand the children a
    // second scene while the canvas kept drawing into the first.
    const runtimeRef = useRef<ViewerRuntime | null>(null)
    if (runtimeRef.current === null) runtimeRef.current = createViewerRuntime()
    const runtime = runtimeRef.current

    // Registered DURING render, not in an effect. Child effects run before the
    // parent's, so a canvas child that reaches an imperative helper from its
    // own mount effect would otherwise resolve `getViewerRuntime()` to the
    // detached fallback and quietly build its scene in a runtime nobody reads.
    const registered = useRef(false)
    if (!registered.current) {
        registered.current = true
        mountViewerRuntime(runtime)
    }
    useEffect(() => mountViewerRuntime(runtime), [runtime])

    const value = useMemo<AdaViewerCtx>(
        () => ({ refs: runtime, stores: SINGLETON_VIEWER_STORES }),
        [runtime],
    )

    return <AdaViewerContext.Provider value={value}>{children}</AdaViewerContext.Provider>
}
