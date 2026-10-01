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
// share one ingest path.
//
// Several mounts per page are supported: each has its own scene-graph
// runtime, and `./viewerInstances` gives each its own store state by
// swapping it into the (still process-global) stores whenever the user
// turns to that viewer. So a panel's controls act on the panel they sit
// in, not on whichever viewer the page opened last.

import * as THREE from "three"
import CameraControls from "camera-controls"
import React from "react"
import { createRoot, type Root } from "react-dom/client"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls"

import "../src/app.css"

import { AdaViewerProvider } from "../src/state/AdaViewerContext"
import { createViewerRuntime, getViewerRuntime } from "../src/state/viewerRuntime"
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
import { useFeaAnimationStore, type FeaAnimationState } from "../src/state/feaAnimationStore"
import {
    resetFeaAnimationPhase,
    stepFeaSweep,
    tickFeaAnimation,
} from "../src/utils/scene/fea/feaAnimationDriver"
import { findMorphPrimitive } from "../src/utils/scene/fea/morphPrimitive"

import { EmbedUI } from "./EmbedUI"
import {
    createEmbedInstance,
    disposeEmbedInstance,
    isActiveInstance,
    parkedState,
    requestActivation,
    withActiveInstance,
} from "./viewerInstances"

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
    // Safety floor for hosts that arrive with no sizing at all. If
    // the caller has set an explicit `style.height` (paradoc's
    // ThreeDRenderer passes `height: '100%'`, expecting its parent
    // chain to constrain the host), we trust them — forcing
    // ``min-height: 400 px`` on a 322 px mobile wrapper made the
    // canvas overflow its `overflow: hidden` ancestor and clip the
    // model. Callers that don't size the host explicitly still get
    // the floor so the canvas can't collapse to 0.
    if (!element.style.minHeight && !element.style.height) {
        element.style.minHeight = `${DEFAULT_MIN_HEIGHT}px`
    }
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

    // --- This viewer's runtime ---
    // adapy's pipeline (setupModelLoaderAsync, prepareLoadedModel,
    // setupPointerHandler, the TreeView/InfoBox React tree) reaches the
    // scene through `getViewerRuntime()` / `useViewerRefs()`; the
    // instance makes this runtime the one they answer with whenever this
    // viewer is the active one.
    const runtime = createViewerRuntime()
    runtime.scene.current = scene
    runtime.camera.current = camera
    runtime.controls.current = controls as unknown as OrbitControls
    runtime.renderer.current = renderer
    runtime.updateLight.current = null

    // Only the active viewer shows its toolbar and panels: an inactive
    // one's overlay would render the active viewer's store state.
    const instance = createEmbedInstance(runtime, (isActive) => {
        overlayHost.style.visibility = isActive ? "" : "hidden"
        element.dataset.adaViewerActive = isActive ? "true" : "false"
    })
    overlayHost.style.visibility = "hidden"
    // Turning to a viewer makes it the active one. Capture phase, so the
    // switch is queued before the viewer's own handlers see the press.
    const activate = () => requestActivation(instance)
    element.addEventListener("pointerenter", activate)
    element.addEventListener("pointerdown", activate, true)
    element.addEventListener("focusin", activate)

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
        const delta = clock.getDelta()
        controls.update(delta)
        // Drive the FEA mode-shape morph each frame when an FEA
        // session is active (set by mountFeaArtefactViewer). No-op
        // when no session is active — same path the standalone
        // viewer uses via ThreeCanvas.tsx. The stores hold only the
        // active viewer's session; an inactive one sweeps its own mesh
        // from its parked session and phase, so a mode left playing
        // keeps playing while the user works in another panel.
        if (isActiveInstance(instance)) {
            tickFeaAnimation(delta)
        } else {
            const fea = parkedState<FeaAnimationState>(instance, "useFeaAnimationStore")
            const stepped = fea ? stepFeaSweep(fea, instance.feaPhase, delta) : null
            if (stepped) instance.feaPhase = stepped.phase
        }
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
    //
    // The whole load runs with this viewer active: the loader and
    // `onReady` write the stores and resolve the scene at call time, so
    // no other viewer may swap its state in until they are done.
    void withActiveInstance(instance, async () => {
        try {
            // --- Pre-configure useModelState so the adapy pipeline doesn't
            //     fight the embed's camera preset framing. Lock translation
            //     so prepareLoadedModel doesn't recenter the model — paradoc
            //     hands us a baked PNG-aligned preset and we frame to that.
            //     These land in this viewer's own store state. ---
            useModelState.getState().setZIsUp(Z_IS_UP)
            useOptionsStore.getState().setLockTranslation(true)

            // --- Embed default: panels start closed, tree drawer collapsed.
            //     The user opens what they need via the toolbar. Mirrors the
            //     paradoc-side "first-paint stays minimal" idiom. ---
            useObjectInfoStore.setState({ show_info_box: false })
            useSceneInfoStore.setState({ show_scene_info_box: false })
            useAnimationStore.getState().setIsControlsVisible(false)
            useTreeViewStore.getState().setIsTreeCollapsed(true)

            const blob = new Blob([new Uint8Array(opts.modelBytes)], {
                type: "model/gltf-binary",
            })
            blobUrl = URL.createObjectURL(blob)

            const modelGroup = await setupModelLoaderAsync({
                modelUrl: blobUrl,
                translate: false,
                // The embed frames to paradoc's camera preset below.
                autoFitOverride: false,
            })
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
                    React.createElement(AdaViewerProvider, {
                        runtime,
                        children: React.createElement(EmbedUI),
                    }),
                )
            }

            tick()
            // A microtask later, as before (the overlay's first commit
            // lands first), but still inside this viewer's turn.
            await Promise.resolve()
            if (disposed) return
            opts.onReady?.()
            // `onReady` is where mountFeaArtefactViewer activates the FEA
            // session and sets ``morphTargetInfluences = [1.0]`` on the
            // mesh. The first ``applyCameraPreset`` above ran before that
            // — its bbox saw the rest-position vertices and framed the
            // un-deformed silhouette, so the deformed tip ended up below
            // the bottom of the viewport. Re-fit here so the framing
            // matches whatever the scene became during ``onReady``. No-op
            // for non-FEA mounts (no morphs → same bbox → same fit).
            if (loadedModelGroup) {
                applyCameraPreset(camera, controls, loadedModelGroup, opts.camera)
            }
        } catch (err) {
            opts.onError?.(err instanceof Error ? err : new Error(String(err)))
        }
    })

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
            element.removeEventListener("pointerenter", activate)
            element.removeEventListener("pointerdown", activate, true)
            element.removeEventListener("focusin", activate)
            // Drop this viewer's state: its runtime leaves the mounted
            // stack, its parked store state goes, and if it was the active
            // one the stores return to their initial state -- so no other
            // viewer, and no later mount, inherits its session, selection
            // or forced lockTranslation, nor a handle into this scene.
            disposeEmbedInstance(instance)
            runtime.scene.current = null
            runtime.camera.current = null
            runtime.controls.current = null
            runtime.renderer.current = null
            delete element.dataset.adaViewerActive
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

// Compute a bounding box that accounts for morph-target displacement
// at current influences. `Box3.setFromObject` reads only rest-position
// vertices, so an FEA mode-shape mesh whose morphTargetInfluences=[1]
// drives the displayed beam below its rest-bbox bottom would frame
// to a too-small box and clip on the bottom edge. The poster pipeline
// (fea_offscreen.py) sidesteps this by baking deformed positions into
// the GLB before render; the live viewer assembles + morphs at
// runtime, so it needs to do the same work here.
function computeRenderableBox(root: THREE.Object3D): THREE.Box3 {
    const box = new THREE.Box3()
    const vert = new THREE.Vector3()
    const baseV = new THREE.Vector3()
    const morphV = new THREE.Vector3()

    root.traverseVisible((obj) => {
        const m = obj as THREE.Mesh
        if (!(m as any).isMesh || !m.geometry) return
        const geom = m.geometry
        const posAttr = geom.attributes.position as THREE.BufferAttribute | undefined
        if (!posAttr) return
        m.updateWorldMatrix(true, false)

        const morphAttrs = geom.morphAttributes?.position as
            | THREE.BufferAttribute[]
            | undefined
        const influences = m.morphTargetInfluences || []
        const hasMorph =
            !!morphAttrs &&
            morphAttrs.length > 0 &&
            influences.length > 0 &&
            influences.some((i) => Math.abs(i) > 1e-9)

        if (!hasMorph) {
            const meshBox = new THREE.Box3().setFromObject(m)
            if (!meshBox.isEmpty()) box.union(meshBox)
            return
        }

        const relative = (geom as any).morphTargetsRelative ?? true
        for (let i = 0; i < posAttr.count; i++) {
            baseV.fromBufferAttribute(posAttr, i)
            vert.copy(baseV)
            for (let mi = 0; mi < morphAttrs.length; mi++) {
                const w = influences[mi] || 0
                if (w === 0) continue
                morphV.fromBufferAttribute(morphAttrs[mi], i)
                if (relative) {
                    vert.x += w * morphV.x
                    vert.y += w * morphV.y
                    vert.z += w * morphV.z
                } else {
                    vert.x += w * (morphV.x - baseV.x)
                    vert.y += w * (morphV.y - baseV.y)
                    vert.z += w * (morphV.z - baseV.z)
                }
            }
            vert.applyMatrix4(m.matrixWorld)
            box.expandByPoint(vert)
        }
    })

    if (box.isEmpty()) box.setFromObject(root)
    return box
}

function applyCameraPreset(
    camera: THREE.PerspectiveCamera,
    controls: CameraControls,
    root: THREE.Object3D,
    preset: CameraPreset,
): void {
    const box = computeRenderableBox(root)
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
 * Walk the loaded scene, find the CustomBatchedMesh that carries the
 * FEA mode-shape morph, finalise its morph wiring, and flip the
 * feaAnimationStore session on. After this runs:
 *
 *   * `feaAnimationStore.sessionActive` is true, which is what
 *     `EmbedUI` / `SimulationControls` gate the FeaModeControls panel
 *     on (the legacy GltfClipControls drop-down stays hidden).
 *   * `feaAnimationStore.mesh` points at the live CustomBatchedMesh,
 *     so `tickFeaAnimation` writes through its morphTargetInfluences.
 *   * The deformation-scale slider's range follows the field's
 *     analysis_kind ([-1, +1] for eigen, [0, 1] for static).
 *
 * Defensive on every step — the embed should still render the mesh
 * at the baked influence=1 baseline if any of this fails.
 */
function activateFeaSession(
    manifest: import("../src/services/viewerApi").FeaManifest,
    modeIndex: number,
): void {
    // Called from `onReady`, inside the loading viewer's turn, so the
    // mounted runtime is that viewer's.
    const scene = getViewerRuntime().scene.current
    if (!scene) return

    // The primitive carrying the mode displacement morph the bake
    // installs: a Mesh, or for a beam model a LineSegments.
    const feaMesh = findMorphPrimitive(scene)
    if (!feaMesh) return

    // prepareLoadedModel only copies morphTargetInfluences onto the
    // CustomBatchedMesh when useAnimationStore.hasAnimation is true
    // AND the mesh is flagged as a sim mesh (ADA_EXT_data present);
    // assembleFeaGlb installs neither, so wire it here. Material flags
    // already set by assembleFeaGlb survive the export/import.
    feaMesh.morphTargetInfluences = [1.0]
    const enableMorph = (mat: THREE.Material) => {
        let dirty = false
        if ("morphTargets" in mat && (mat as any).morphTargets !== true) {
            ;(mat as any).morphTargets = true
            dirty = true
        }
        if ("vertexColors" in mat && (mat as any).vertexColors !== true) {
            ;(mat as any).vertexColors = true
            dirty = true
        }
        if (dirty) mat.needsUpdate = true
    }
    if (Array.isArray(feaMesh.material)) {
        feaMesh.material.forEach(enableMorph)
    } else if (feaMesh.material) {
        enableMorph(feaMesh.material as THREE.Material)
    }

    // Re-share the morphTargetInfluences array with the wireframe-edge
    // LineSegments child. The GLB roundtrip gave them independent arrays
    // (glTF stores weights per-mesh), so a single writer (the driver)
    // would only morph the surface and the edges would stay un-deformed.
    // Same pattern as load_fea_streaming's assignMorphToEdgeAlso.
    feaMesh.traverse((child) => {
        const c = child as any
        if (c === feaMesh) return
        if (
            c.isLineSegments &&
            c.geometry?.morphAttributes?.position?.length > 0
        ) {
            c.morphTargetInfluences = feaMesh.morphTargetInfluences
            if (c.material) {
                const lm = c.material as THREE.Material
                if ("morphTargets" in lm) {
                    ;(lm as any).morphTargets = true
                    lm.needsUpdate = true
                }
            }
        }
    })

    // Field range follows the active field's analysis_kind. Match the
    // global modeIndex to the (field, step) the bake actually
    // rendered so the slider's polarity matches the displayed mode.
    let analysisKind: "static" | "eigen" = "eigen"
    let fieldName: string | null = null
    let nSteps = 1
    let stepIndex = 0
    if (manifest?.fields?.length) {
        const dispFields = manifest.fields.filter(
            (f: any) =>
                f.blob &&
                (f.category === "displacement" ||
                    /U|DEPL|DISPLACEMENT/i.test(f.name_canonical || "")),
        )
        let seen = 0
        for (const f of dispFields) {
            const n = Math.max(1, f.n_steps | 0)
            if (modeIndex < seen + n) {
                analysisKind = (f as any).analysis_kind || "eigen"
                fieldName = (f as any).name_canonical || null
                stepIndex = modeIndex - seen
                break
            }
            seen += n
        }
        nSteps = dispFields.reduce(
            (acc: number, f: any) => acc + Math.max(1, f.n_steps | 0),
            0,
        )
    }

    const range: [number, number] =
        analysisKind === "static" ? [0, 1] : [-1, 1]

    resetFeaAnimationPhase()
    const s = useFeaAnimationStore.getState()
    s.setMesh(feaMesh as unknown as THREE.Mesh)
    s.setRange(range)
    s.setFactor(1.0)
    s.setNSteps(Math.max(1, nSteps))
    s.setStepIndex(stepIndex)
    s.setFieldName(fieldName)
    s.setManifest(manifest as never)
    s.setIsPlaying(false)
    s.setSessionActive(true)
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
            const userOnReady = opts.onReady
            inner = mountViewer(element, {
                modelBytes,
                camera: opts.camera,
                caption: opts.caption,
                showControls: opts.showControls,
                onReady: () => {
                    // Hook the live scene up to the feaAnimationStore so
                    // SimulationControls' FeaModeControls panel surfaces
                    // (sessionActive=true) and its deformation-scale
                    // slider / play sweep drive the morph influence in
                    // sync with the standalone viewer's REST path.
                    if (!disposed) {
                        try {
                            activateFeaSession(
                                opts.manifest as never,
                                opts.modeIndex ?? 0,
                            )
                        } catch (err) {
                            // Session activation is best-effort —
                            // without it the mesh still renders at the
                            // initial morphTargetInfluences=1 baseline,
                            // just without the slider UI.
                            // eslint-disable-next-line no-console
                            console.warn(
                                "[fea-embed] feaAnimationStore wiring failed:",
                                err,
                            )
                        }
                    }
                    userOnReady?.()
                },
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
            // No FEA-store reset here: the store may hold ANOTHER viewer's
            // session by now. The inner viewer's dispose drops this one's
            // state (and resets the stores if it was the active viewer).
            try {
                inner?.dispose()
            } catch {
                /* inner mount may not have completed yet */
            }
        },
    }
}
