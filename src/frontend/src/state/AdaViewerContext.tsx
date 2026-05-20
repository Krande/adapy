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

import React, { createContext, useContext, useMemo, type ReactNode, type RefObject } from "react"
import CameraControls from "camera-controls"
import * as THREE from "three"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls"

import { AnimationController } from "../utils/scene/animations/AnimationController"
import {
    ADADesignAndAnalysisExtension,
    SimulationDataExtensionMetadata,
} from "../extensions/design_and_analysis_extension"

import {
    adaExtensionRef as g_adaExtensionRef,
    animationControllerRef as g_animationControllerRef,
    cameraRef as g_cameraRef,
    controlsRef as g_controlsRef,
    modelKeyMapRef as g_modelKeyMapRef,
    rendererRef as g_rendererRef,
    sceneRef as g_sceneRef,
    selectedPointRef as g_selectedPointRef,
    simulationDataRef as g_simulationDataRef,
    updatelightRef as g_updatelightRef,
} from "./refs"

import { useAdminPanelStore as g_useAdminPanelStore } from "./adminPanelStore"
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
 * Per-instance React refs that today live as module-level singletons
 * in `state/refs.ts`. The shape mirrors the singleton names 1:1 so
 * the migration is mechanical: `import {sceneRef} from "@/state/refs"`
 * becomes `const {scene} = useViewerRefs()` (refs are still
 * `RefObject<T | null>`, semantics are unchanged).
 */
export interface AdaViewerRefs {
    scene: RefObject<THREE.Scene | null>
    camera: RefObject<THREE.PerspectiveCamera | null>
    controls: RefObject<CameraControls | OrbitControls | null>
    renderer: RefObject<THREE.WebGLRenderer | null>
    updateLight: RefObject<(() => void) | null>
    animationController: RefObject<AnimationController | null>
    simulationData: RefObject<SimulationDataExtensionMetadata | null>
    adaExtension: RefObject<ADADesignAndAnalysisExtension | null>
    modelKeyMap: RefObject<Map<string, THREE.Object3D | THREE.Group> | null>
    selectedPoint: RefObject<THREE.Points | null>
}

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
    useAdminPanelStore: typeof g_useAdminPanelStore
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
    const value = useMemo<AdaViewerCtx>(
        () => ({
            refs: {
                scene: g_sceneRef,
                camera: g_cameraRef,
                controls: g_controlsRef,
                renderer: g_rendererRef,
                updateLight: g_updatelightRef,
                animationController: g_animationControllerRef,
                simulationData: g_simulationDataRef,
                adaExtension: g_adaExtensionRef,
                modelKeyMap: g_modelKeyMapRef,
                selectedPoint: g_selectedPointRef,
            },
            stores: {
                useAdminPanelStore: g_useAdminPanelStore,
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
            },
        }),
        [],
    )

    return <AdaViewerContext.Provider value={value}>{children}</AdaViewerContext.Provider>
}
