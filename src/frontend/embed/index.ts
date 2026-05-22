// Embedded ada-py viewer — three.js GLB renderer with paradoc-shaped
// `mountViewer` API:
//
//   mountViewer(element, { modelBytes, camera, showControls?, ...}):
//       { dispose() }
//
// Two modes:
//
//   * showControls: false (default) — canvas + CameraControls only.
//     Smallest possible footprint, runs the model through adapy's
//     ingest pipeline so panels would work if turned on later.
//
//   * showControls: true — overlays adapy's selection tree + object
//     info + group info panels over the canvas. Clicks on the model
//     populate the same zustand stores the standalone app uses.
//
// Both modes drive everything through adapy's existing pipelines
// (setupModelLoaderAsync, prepareLoadedModel, setupPointerHandler,
// cacheAndBuildTree) so paradoc's embed and adapy's standalone viewer
// share one ingest path. Phase 1 of `AdaViewerProvider` makes the
// store / ref singletons context-routed but they're still process-
// global; this embed assumes one mount per page, which is the
// paradoc usage today. Phase 3 will switch to per-instance stores.

import * as THREE from "three"
import CameraControls from "camera-controls"
import React from "react"
import { createRoot, type Root } from "react-dom/client"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls"

import "../src/app.css"

import { AdaViewerProvider } from "../src/state/AdaViewerContext"
import {
    sceneRef,
    cameraRef,
    controlsRef,
    rendererRef,
    updatelightRef,
} from "../src/state/refs"
import { useModelState } from "../src/state/modelState"
import { useOptionsStore } from "../src/state/optionsStore"
import { useObjectInfoStore } from "../src/state/objectInfoStore"
import { useSceneInfoStore } from "../src/state/sceneInfoStore"
import { useAnimationStore } from "../src/state/animationStore"
import { useTreeViewStore } from "../src/state/treeViewStore"

import { setupModelLoaderAsync } from "../src/components/viewer/sceneHelpers/setupModelLoader"
import { setupPointerHandler } from "../src/components/viewer/sceneHelpers/setupPointerHandler"
import { applyStandardLayers } from "../src/components/viewer/sceneHelpers/setupCamera"
import { setupCameraControlsHandlers } from "../src/components/viewer/sceneHelpers/setupCameraControlsHandlers"

import { EmbedUI } from "./EmbedUI"

// adapy emits Z-up GLBs by convention (matches FEA / CAD), so the
// embed orients the camera accordingly. Single-instance assumption.
CameraControls.install({ THREE })
const Z_IS_UP = true

export interface CameraPreset {
    name: string
    azimuth_deg: number
    elevation_deg: number
    roll_deg?: number
    target?: "bbox_center"
    distance?: "fit" | number
    fov_deg?: number
    margin?: number
}

export interface MountViewerOptions {
    modelBytes: Uint8Array
    camera: CameraPreset
    caption?: string
    showControls?: boolean
    onReady?: () => void
    onError?: (err: Error) => void
}

export interface MountedViewer {
    dispose: () => void
}

/**
 * Storage-layer fetcher for FEA artefact bundles. Resolves a
 * manifest-relative filename to raw bytes.
 *
 *   * paradoc passes a fetcher that hits paradoc-serve's
 *     `/api/docs/{id}/3d/{key}/fea/{filename}` endpoint (REST) or a
 *     relative URL under the SPA's asset base (static-mode bundles).
 *   * Standalone adapy-viewer wraps `viewerApi.getBlob` with the
 *     bake-job's `_derived/<src>.fea/` prefix.
 */
export type FeaArtefactFetcher = (filename: string) => Promise<ArrayBuffer>

export interface MountFeaArtefactViewerOptions {
    /** Pre-fetched `fea.manifest.json` body. Caller is responsible
     *  for fetching the manifest (so it can be polled / cached
     *  alongside the rest of paradoc's transport layer). */
    manifest: unknown
    /** Resolves manifest-relative filenames to bytes. */
    fetcher: FeaArtefactFetcher
    camera: CameraPreset
    caption?: string
    showControls?: boolean
    /** 0-based mode index to render (default 0 = first mode). Lets
     *  per-mode figures share one bundle on disk while showing
     *  different deformations inline. */
    modeIndex?: number
    onReady?: () => void
    onError?: (err: Error) => void
}

const DEFAULT_FOV = 50
const DEFAULT_MARGIN = 1.15
const DEFAULT_MIN_HEIGHT = 400

export function mountViewer(element: HTMLElement, opts: MountViewerOptions): MountedViewer {
    let disposed = false
    let reactRoot: Root | null = null
    let blobUrl: string | null = null
    let cleanupPointer: (() => void) | null = null
    let cleanupShortcuts: (() => void) | null = null

    // Inject the embed's CSS (Tailwind + adapy component styles) the first
    // time mountViewer runs. The `inlineCssAtRuntime` vite plugin defines
    // `globalThis.__adaViewerEmbedInjectCss` and idempotently appends one
    // `<style data-ada-viewer-embed>` to the host document. Doing this at
    // mount (not at module-load) prevents the host page's display utilities
    // from being clobbered before any viewer is even on screen.
    //
    // The injected stylesheet is wrapped in `@scope (.ada-viewer-scope)`
    // by the build plugin, so EVERY selector inside only matches
    // descendants of an element carrying the `ada-viewer-scope` class.
    // We add that class to the host element here — without it the canvas
    // mounts but renders unstyled. With it, the host page is fully
    // insulated from our Tailwind reset / utility classes.
    element.classList.add("ada-viewer-scope")
    try {
        ;(globalThis as any).__adaViewerEmbedInjectCss?.()
    } catch {
        /* injector missing — bundle was inlined oddly, or already injected */
    }

    // --- DOM scaffolding ---
    element.innerHTML = ""
    if (!element.style.minHeight) element.style.minHeight = `${DEFAULT_MIN_HEIGHT}px`
    element.style.position = "relative"
    element.style.overflow = "hidden"

    const canvasHost = document.createElement("div")
    canvasHost.style.position = "absolute"
    canvasHost.style.inset = "0"
    canvasHost.style.zIndex = "0"
    element.appendChild(canvasHost)

    // Overlay host for the React UI when showControls is true. Always
    // created (cheap) so the dispose path doesn't have to branch.
    const overlayHost = document.createElement("div")
    overlayHost.style.position = "absolute"
    overlayHost.style.inset = "0"
    overlayHost.style.zIndex = "1"
    overlayHost.style.pointerEvents = "none"
    element.appendChild(overlayHost)

    // --- Renderer / scene / camera ---
    const fov = opts.camera.fov_deg ?? DEFAULT_FOV
    const initialWidth = Math.max(element.clientWidth, 320)
    const initialHeight = Math.max(element.clientHeight, DEFAULT_MIN_HEIGHT)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(initialWidth, initialHeight, false)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.0
    canvasHost.appendChild(renderer.domElement)
    // setSize(_, _, false) leaves canvas.style untouched, so the canvas
    // renders at its attribute width/height (CSS-pixel * devicePixelRatio).
    // On a 320-CSS-pixel-wide viewport with DPR=2 that's a 640px canvas,
    // overflowing the page. Pin the canvas to fill canvasHost (which is
    // already `position: absolute; inset: 0`) so it tracks the container's
    // layout-controlled size — the internal pixel buffer stays at full
    // resolution × DPR for crisp rendering. `!important` because
    // downstream pipeline (setupModelLoaderAsync, CameraControls
    // attach) occasionally clears canvas.style.width/height during
    // its own setup and we want our size to win.
    renderer.domElement.style.setProperty("display", "block", "important")
    renderer.domElement.style.setProperty("width", "100%", "important")
    renderer.domElement.style.setProperty("height", "100%", "important")

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xf8fafc)

    const camera = new THREE.PerspectiveCamera(
        fov,
        initialWidth / initialHeight,
        0.01,
        10000,
    )
    if (Z_IS_UP) camera.up.set(0, 0, 1)
    // Standalone `setupCamera` already does this; the embed open-codes
    // its camera (paradoc passes the FOV from its preset) so we have
    // to call the shared helper explicitly. Without layer 1 enabled,
    // the GLB edge overlay LineSegments (parked on layer 1 by
    // prepareLoadedModel for non-pickability) silently disappear,
    // which is why FEA mesh wireframes were missing in paradoc-embed
    // (REST + static) but visible in the standalone app.
    applyStandardLayers(camera)

    // --- Lights ---
    // Match the standalone adapy viewer's lighting verbatim
    // (`sceneHelpers/setupLights.ts` + `sceneHelpers/addCameraLight.ts`):
    // a bright ambient (`π/2 ≈ 1.57`) plus a camera-tracking key
    // light at intensity 1.4 that re-positions every frame to follow
    // the camera. The earlier `HemisphereLight(0.85) + static sun`
    // setup read as "missing lighting" next to the brighter
    // standalone — same materials, fewer photons, so faces went
    // matte-flat once the camera moved off the sun's azimuth.
    scene.add(new THREE.AmbientLight(0xffffff, Math.PI / 2))
    const cameraLight = new THREE.DirectionalLight(0xffffff, 1.4)
    cameraLight.castShadow = false
    scene.add(cameraLight)
    const cameraLightTarget = new THREE.Object3D()
    scene.add(cameraLightTarget)
    cameraLight.target = cameraLightTarget
    cameraLight.position.copy(camera.position)
    // Each frame the tick loop calls this; keeps the key light
    // pinned to the camera so the user always sees lit surfaces
    // wherever they orbit.
    const updateCameraLight = () => {
        cameraLight.position.copy(camera.position)
        const direction = new THREE.Vector3()
        camera.getWorldDirection(direction)
        cameraLightTarget.position.copy(camera.position.clone().add(direction))
        cameraLight.target.updateMatrixWorld()
    }

    // --- Controls ---
    const controls = new CameraControls(camera, renderer.domElement)
    controls.dollyToCursor = true
    if (Z_IS_UP) controls.updateCameraUp()
    const clock = new THREE.Clock()

    // Wire the standalone's Shift+key shortcuts (Shift+T tree, Shift+F
    // fit-to-selection, Shift+A zoom-all, Shift+H hide selection,
    // Shift+U unhide, Shift+Q options, Shift+C copy names), but
    // scoped to the embed's mount element so they only fire while
    // the user is interacting with the viewer. Without the scope the
    // shortcuts would leak into paradoc — Shift+T would toggle the
    // adapy tree while the reader user was typing in a search box.
    cleanupShortcuts = setupCameraControlsHandlers(
        scene,
        camera,
        controls as unknown as OrbitControls,
        element,
    )

    // --- Populate the singleton refs ---
    // adapy's pipeline (setupModelLoaderAsync, prepareLoadedModel,
    // setupPointerHandler, the Menu/TreeView/InfoBox React tree)
    // reads these via context which today delegates to the same
    // singletons. One mount per page (phase 3 will make this safe
    // for multi-instance via createStore factories).
    sceneRef.current = scene
    cameraRef.current = camera
    controlsRef.current = controls as unknown as OrbitControls
    rendererRef.current = renderer
    updatelightRef.current = null

    // --- Pre-configure useModelState so the adapy pipeline doesn't
    //     fight the embed's camera preset framing. Lock translation
    //     so prepareLoadedModel doesn't recenter the model — paradoc
    //     hands us a baked PNG-aligned preset and we frame to that. ---
    const modelStore = useModelState.getState()
    modelStore.setZIsUp(Z_IS_UP)
    const optionsStore = useOptionsStore.getState()
    const priorLockTranslation = optionsStore.lockTranslation
    optionsStore.setLockTranslation(true)

    // --- Embed default: panels start closed, tree drawer collapsed.
    //     The user opens what they need via the toolbar. Mirrors the
    //     paradoc-side "first-paint stays minimal" idiom. ---
    useObjectInfoStore.setState({ show_info_box: false })
    useSceneInfoStore.setState({ show_scene_info_box: false })
    useAnimationStore.getState().setIsControlsVisible(false)
    useTreeViewStore.getState().setIsTreeCollapsed(true)

    // --- Resize handling ---
    // `loadedModelGroup` is populated by the GLB load below; it's
    // what `applyCameraPreset` needs to compute the model's bbox and
    // refit the camera distance for the new aspect. Without the
    // refit, resizing the figure's wrapper just stretches the canvas
    // — the model stays at its original framing distance and can end
    // up off-centre or partially out of frame in the new dimensions.
    let loadedModelGroup: THREE.Object3D | null = null
    const onResize = () => {
        const w = element.clientWidth || initialWidth
        const h = Math.max(element.clientHeight, DEFAULT_MIN_HEIGHT)
        renderer.setSize(w, h, false)
        camera.aspect = w / h
        camera.updateProjectionMatrix()
        if (loadedModelGroup) {
            // Re-frame so the model stays centred + fully visible in
            // the new viewport. This does override a user's manual
            // orbit, but the alternative (off-centre crop after a
            // resize) is a more common annoyance for documents where
            // figures get resized to compare against neighbouring
            // text. If preserving orbit on resize becomes a real
            // ask, gate on a "user has interacted with controls" flag.
            applyCameraPreset(camera, controls, loadedModelGroup, opts.camera)
        }
    }
    const ro = new ResizeObserver(onResize)
    ro.observe(element)

    // --- Render loop ---
    let frame = 0
    const tick = () => {
        if (disposed) return
        frame = requestAnimationFrame(tick)
        controls.update(clock.getDelta())
        // Reposition the key light to track the camera each frame —
        // matches the standalone viewer's lighting feel.
        updateCameraLight()
        renderer.render(scene, camera)
    }

    // --- Load the GLB through adapy's pipeline ---
    // Building a Blob URL lets us reuse setupModelLoaderAsync, which
    // handles ADA_EXT_data extraction, prepareLoadedModel, and the
    // worker-cache tree build that the TreeView panel reads. Without
    // that pipeline the panels would render empty even with the
    // refs populated.
    ;(async () => {
        try {
            const blob = new Blob([new Uint8Array(opts.modelBytes)], {
                type: "model/gltf-binary",
            })
            blobUrl = URL.createObjectURL(blob)

            const modelGroup = await setupModelLoaderAsync(blobUrl, false)
            if (disposed) return

            // Hand the model to the resize handler so subsequent
            // container resizes can re-frame against this same group.
            loadedModelGroup = modelGroup
            applyCameraPreset(camera, controls, modelGroup, opts.camera)

            // Pointer / raycaster selection. Updates objectInfoStore
            // + selectedObjectStore on click — same path the standalone
            // app uses, so the EmbedUI's info panel lights up the
            // moment the user clicks a face.
            cleanupPointer = setupPointerHandler(
                canvasHost,
                camera,
                scene,
                renderer,
                controls,
            )

            // Mount the overlay UI after the model is in place so the
            // tree-view store has been populated by cacheAndBuildTree.
            if (opts.showControls) {
                reactRoot = createRoot(overlayHost)
                reactRoot.render(
                    React.createElement(AdaViewerProvider, null, React.createElement(EmbedUI)),
                )
            }

            tick()
            queueMicrotask(() => {
                if (!disposed) opts.onReady?.()
            })
        } catch (err) {
            opts.onError?.(err instanceof Error ? err : new Error(String(err)))
        }
    })()

    return {
        dispose() {
            if (disposed) return
            disposed = true
            cancelAnimationFrame(frame)
            ro.disconnect()
            try {
                cleanupPointer?.()
            } catch {
                /* ignore */
            }
            try {
                cleanupShortcuts?.()
            } catch {
                /* ignore */
            }
            try {
                reactRoot?.unmount()
            } catch {
                /* ignore */
            }
            controls.dispose()
            // Drop refs so a subsequent mount on the same page gets a
            // clean slate. Phase 3 (per-instance stores via createStore
            // factories) makes the explicit teardown unnecessary; for
            // now this is the discipline that keeps a navigated-away
            // viewer from leaking the WebGL context through stale
            // sceneRef/rendererRef captures.
            if (sceneRef.current === scene) sceneRef.current = null
            if (cameraRef.current === camera) cameraRef.current = null
            if ((controlsRef.current as unknown) === controls) controlsRef.current = null
            if (rendererRef.current === renderer) rendererRef.current = null
            // Restore the user's option so a re-mount doesn't inherit
            // our forced lockTranslation=true.
            try {
                useOptionsStore.getState().setLockTranslation(priorLockTranslation)
            } catch {
                /* store may already be torn down in test envs */
            }
            renderer.dispose()
            if (blobUrl) {
                try {
                    URL.revokeObjectURL(blobUrl)
                } catch {
                    /* ignore */
                }
                blobUrl = null
            }
            try {
                element.removeChild(canvasHost)
            } catch {
                /* already detached */
            }
            try {
                element.removeChild(overlayHost)
            } catch {
                /* already detached */
            }
            // Drop the scope class so the host element looks the same
            // before and after the viewer's lifecycle.
            try {
                element.classList.remove("ada-viewer-scope")
            } catch {
                /* ignore */
            }
        },
    }
}

function applyCameraPreset(
    camera: THREE.PerspectiveCamera,
    controls: CameraControls,
    root: THREE.Object3D,
    preset: CameraPreset,
): void {
    const box = new THREE.Box3().setFromObject(root)
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3())
    const radius = Math.max(size.length() * 0.5, 1e-3)

    const margin = preset.margin ?? DEFAULT_MARGIN
    const fovRad = (camera.fov * Math.PI) / 180
    let distance: number
    if (typeof preset.distance === "number") {
        distance = preset.distance
    } else {
        const fitV = radius / Math.sin(fovRad / 2)
        const fitH = radius / Math.sin(Math.atan(Math.tan(fovRad / 2) * camera.aspect))
        distance = Math.max(fitV, fitH) * margin
    }

    const az = (preset.azimuth_deg * Math.PI) / 180
    const el = (preset.elevation_deg * Math.PI) / 180
    const offset = Z_IS_UP
        ? new THREE.Vector3(
              distance * Math.cos(el) * Math.sin(az),
              distance * Math.cos(el) * Math.cos(az),
              distance * Math.sin(el),
          )
        : new THREE.Vector3(
              distance * Math.cos(el) * Math.sin(az),
              distance * Math.sin(el),
              distance * Math.cos(el) * Math.cos(az),
          )
    const position = new THREE.Vector3().copy(center).add(offset)

    camera.near = Math.max(distance / 1000, 1e-3)
    camera.far = distance * 100
    camera.updateProjectionMatrix()

    controls.setLookAt(
        position.x,
        position.y,
        position.z,
        center.x,
        center.y,
        center.z,
        false,
    )
    controls.minDistance = distance * 0.05
    controls.maxDistance = distance * 20
}


/**
 * Mount a viewer fed by a pre-baked FEA artefact bundle.
 *
 * The standalone adapy-viewer loads bundles directly through
 * `load_fea_streaming.ts`, mutating its singleton scene + multiple
 * stores. paradoc-embed can't do that — it doesn't own the singleton
 * scene and stores. Instead, this helper:
 *
 *   1. Calls `assembleAnimatedFeaGlb(fetcher, manifest)`, which
 *      fetches the mesh GLB + displacement field blob + edge sidecar
 *      and assembles a single consolidated GLB carrying one morph
 *      target per mode + one glTF animation clip per mode + the
 *      wireframe as a child LineSegments.
 *   2. Routes the assembled bytes through the existing
 *      `mountViewer({modelBytes})` flow — the embed sees a normal
 *      animated GLB, the loader flips `hasAnimation = true`, and the
 *      SimulationControls UI lights up under `showControls: true`.
 *
 * No new scene / store wiring; future work refactors `load_fea_streaming`
 * to share its core orchestration with this path so the assembled
 * GLB lives only in the byte stream, not in two parallel pipelines.
 */
export function mountFeaArtefactViewer(
    element: HTMLElement,
    opts: MountFeaArtefactViewerOptions,
): MountedViewer {
    let disposed = false
    let inner: MountedViewer | null = null

    // Show a "loading" hint so the user has feedback while the
    // assembly runs (mesh + field-blob fetch + GLTFExporter pass can
    // take a couple of seconds on a large model). Cleared once the
    // inner viewer mounts.
    element.innerHTML = ""
    const loading = document.createElement("div")
    loading.style.cssText =
        "display:flex;align-items:center;justify-content:center;width:100%;height:100%;" +
        "min-height:200px;color:#666;font:14px system-ui;"
    loading.textContent = "Loading FEA mode shapes…"
    element.appendChild(loading)

    ;(async () => {
        try {
            // Lazy import so a host that never asks for FEA doesn't
            // pay the THREE.GLTFExporter + parser bundle cost on
            // page load.
            const {assembleAnimatedFeaGlb} = await import(
                "../src/utils/scene/fea/assembleFeaGlb"
            )
            const modelBytes = await assembleAnimatedFeaGlb(
                opts.fetcher,
                opts.manifest as never, // typed loosely on the public API
                opts.modeIndex ?? 0,
            )
            if (disposed) return
            inner = mountViewer(element, {
                modelBytes,
                camera: opts.camera,
                caption: opts.caption,
                showControls: opts.showControls,
                onReady: opts.onReady,
                onError: opts.onError,
            })
        } catch (err) {
            if (disposed) return
            const message = err instanceof Error ? err.message : String(err)
            element.innerHTML = ""
            const errBox = document.createElement("div")
            errBox.style.cssText =
                "padding:1rem;color:#b91c1c;font:14px system-ui;" +
                "background:#fef2f2;border:1px solid #fecaca;border-radius:6px;"
            errBox.textContent = `Failed to load FEA bundle: ${message}`
            element.appendChild(errBox)
            opts.onError?.(err instanceof Error ? err : new Error(message))
        }
    })()

    return {
        dispose: () => {
            disposed = true
            try {
                inner?.dispose()
            } catch {
                /* inner mount may not have completed yet */
            }
        },
    }
}
