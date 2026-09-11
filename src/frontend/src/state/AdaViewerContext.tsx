// Phase 1 scaffold for the `<adapy-viewer>` web-component refactor.
//
// Today every component reaches into module-level singletons:
//   - `state/refs.ts` exports React refs (sceneRef, cameraRef, ...)
//   - `state/*Store.ts` files export zustand hooks via `create()`
//
// That's fine for a single full-page app but breaks the moment you
// want two viewers on the same page (paradoc embedding N FEA mode
// shapes, multi-tab dashboards, etc) — both instances trample each
// other's refs and stores.
//
// This file introduces `AdaViewerContext` + `AdaViewerProvider`
// without changing any behavior. Phase 1 wires the provider to the
// existing globals, so `useViewerRefs()` returns the same `sceneRef`
// objects every direct importer already uses. Phase 2 migrates
// consumers off the direct imports one subsystem at a time. Phase 3
// flips the provider to per-instance refs + zustand factories and
// drops the singletons.
//
// Anything new should consume the context, not the singletons.

import React, { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react"

import {
    createViewerRuntime,
    mountViewerRuntime,
    type ViewerRuntime,
} from "./viewerRuntime"

import { useAnimationStore as g_useAnimationStore } from "./animationStore"
import { useColorStore as g_useColorStore } from "./colorLegendStore"
import { useCompressionStore as g_useCompressionStore } from "./compressionStore"
import { useConversionStore as g_useConversionStore } from "./conversionStore"
import { useExperimentalStore as g_useExperimentalStore } from "./experimentalStore"
import { useFeaAnimationStore as g_useFeaAnimationStore } from "./feaAnimationStore"
import { useSceneInfoStore as g_useSceneInfoStore } from "./sceneInfoStore"
import { useLineageStore as g_useLineageStore } from "./lineageStore"
import { useMeStore as g_useMeStore } from "./meStore"
import { useModelState as g_useModelState } from "./modelState"
import { useObjectInfoStore as g_useObjectInfoStore } from "./objectInfoStore"
import { useOptionsStore as g_useOptionsStore } from "./optionsStore"
import { usePerfStore as g_usePerfStore } from "./perfStore"
import { useScopeStore as g_useScopeStore } from "./scopeStore"
import { useServerInfoStore as g_useServerInfoStore } from "./serverInfoStore"
import { useTableNavStore as g_useTableNavStore } from "./tableNavStore"
import { useTreeViewStore as g_useTreeViewStore } from "./treeViewStore"
import { useNodeEditorStore as g_useNodeEditorStore } from "./useNodeEditorStore"
import { useSelectedObjectStore as g_useSelectedObjectStore } from "./useSelectedObjectStore"
import { useWebsocketStatusStore as g_useWebsocketStatusStore } from "./websocketStatusStore"
import { useWebSocketStore as g_useWebSocketStore } from "./webSocketStore"


/**
 * One viewer instance's scene-graph handles — the shape `useViewerRefs()`
 * returns. Defined by `ViewerRuntime` in `./viewerRuntime`, which is also what
 * the imperative (non-React) modules read through `getViewerRuntime()`; the
 * alias is kept because `@/viewer-core/app` publishes this name to out-of-tree
 * UI shells.
 */
export type AdaViewerRefs = ViewerRuntime

/**
 * Zustand store hooks, indexed by short name. In Phase 1 these are
 * the singleton `create()` hooks; Phase 2 swaps each one to a
 * `createStore()` factory + `useStore(api, selector)` adapter so two
 * `<adapy-viewer>` instances on the same page get isolated state.
 *
 * Hook *names* deliberately match the existing exports
 * (`useModelState`, not `useModel`) so a consumer migrating off the
 * direct import doesn't have to rename call sites.
 */
export interface AdaViewerStores {
    useAnimationStore: typeof g_useAnimationStore
    useColorStore: typeof g_useColorStore
    useCompressionStore: typeof g_useCompressionStore
    useConversionStore: typeof g_useConversionStore
    useExperimentalStore: typeof g_useExperimentalStore
    useFeaAnimationStore: typeof g_useFeaAnimationStore
    useSceneInfoStore: typeof g_useSceneInfoStore
    useLineageStore: typeof g_useLineageStore
    useMeStore: typeof g_useMeStore
    useModelState: typeof g_useModelState
    useObjectInfoStore: typeof g_useObjectInfoStore
    useOptionsStore: typeof g_useOptionsStore
    usePerfStore: typeof g_usePerfStore
    useScopeStore: typeof g_useScopeStore
    useServerInfoStore: typeof g_useServerInfoStore
    useTableNavStore: typeof g_useTableNavStore
    useTreeViewStore: typeof g_useTreeViewStore
    useNodeEditorStore: typeof g_useNodeEditorStore
    useSelectedObjectStore: typeof g_useSelectedObjectStore
    useWebsocketStatusStore: typeof g_useWebsocketStatusStore
    useWebSocketStore: typeof g_useWebSocketStore
}

export interface AdaViewerCtx {
    refs: AdaViewerRefs
    stores: AdaViewerStores
}

const AdaViewerContext = createContext<AdaViewerCtx | null>(null)


/**
 * The Phase-1 singleton store bag, built once. The provider hands this to React
 * consumers via `useViewerStores()`; non-React callers (e.g. the plugin
 * sidecar-loader run-point, which fires mid model-load outside the React tree)
 * read it through `getSingletonViewerStores()`. Both paths therefore see the
 * exact same store instances. Phase 2/3 replaces this with per-instance
 * `createStore()` factories built inside the provider.
 */
export const SINGLETON_VIEWER_STORES: AdaViewerStores = {
    useAnimationStore: g_useAnimationStore,
    useColorStore: g_useColorStore,
    useCompressionStore: g_useCompressionStore,
    useConversionStore: g_useConversionStore,
    useExperimentalStore: g_useExperimentalStore,
    useFeaAnimationStore: g_useFeaAnimationStore,
    useSceneInfoStore: g_useSceneInfoStore,
    useLineageStore: g_useLineageStore,
    useMeStore: g_useMeStore,
    useModelState: g_useModelState,
    useObjectInfoStore: g_useObjectInfoStore,
    useOptionsStore: g_useOptionsStore,
    usePerfStore: g_usePerfStore,
    useScopeStore: g_useScopeStore,
    useServerInfoStore: g_useServerInfoStore,
    useTableNavStore: g_useTableNavStore,
    useTreeViewStore: g_useTreeViewStore,
    useNodeEditorStore: g_useNodeEditorStore,
    useSelectedObjectStore: g_useSelectedObjectStore,
    useWebsocketStatusStore: g_useWebsocketStatusStore,
    useWebSocketStore: g_useWebSocketStore,
}

/** Non-hook accessor for the Phase-1 singleton store bag (see above). */
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

/** Per-instance refs (today a thin pass-through to `state/refs.ts`). */
export function useViewerRefs(): AdaViewerRefs {
    return useAdaViewerCtx().refs
}

/** Per-instance zustand hooks (today a thin pass-through to the singletons). */
export function useViewerStores(): AdaViewerStores {
    return useAdaViewerCtx().stores
}


/**
 * Phase-1 provider: hands consumers the same module-level singletons
 * they already import directly. Behavior is identical; the indirection
 * only exists so Phase-2 migrations can move one consumer at a time
 * without churning the rest of the app.
 *
 * Once every consumer reads through the context, this provider's body
 * gets replaced with `createStore()` factories and a fresh ref bag per
 * mount, and the singletons in `state/refs.ts` + `state/*Store.ts`
 * get deleted.
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
