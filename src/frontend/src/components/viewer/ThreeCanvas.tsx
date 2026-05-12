// ThreeCanvas.tsx
import React, {useEffect, useRef} from "react";
import * as THREE from "three";
import {useModelState} from "@/state/modelState";
import {useOptionsStore} from "@/state/optionsStore";
import {runtime} from "@/runtime/config";
import {OrientationGizmo} from "./sceneHelpers/OrientationGizmo";
import {setupCameraControlsHandlers} from "./sceneHelpers/setupCameraControlsHandlers";
import {setupCamera} from "./sceneHelpers/setupCamera";
import {setupControls} from "./sceneHelpers/setupControls";
import {setupLights} from "./sceneHelpers/setupLights";
import {addDynamicGridHelper} from "./sceneHelpers/addDynamicGridHelper";
import {setupGizmo} from "./sceneHelpers/setupGizmo";
import {setupStats} from "./sceneHelpers/setupStats";
import {setupResizeHandler} from "./sceneHelpers/setupResizeHandler";
import {setupPointerHandler} from "./sceneHelpers/setupPointerHandler";
import {animationControllerRef, cameraRef, controlsRef, rendererRef, sceneRef, updatelightRef} from "@/state/refs";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {AnimationController} from "@/utils/scene/animations/AnimationController";
import {replace_model} from "@/utils/scene/handlers/update_scene_from_message";
import {tickFeaAnimation} from "@/utils/scene/fea/feaAnimationDriver";
import {consumeDirty, requestRender, usePerfStore} from "@/state/perfStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";


const ThreeCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const {modelUrl, zIsUp, defaultOrbitController} = useModelState();
    const {showPerf} = useOptionsStore();
    const modelGroupRef = useRef<THREE.Group | null>(null); // <-- store loaded model separately
    const statsRef = useRef<{
        statsArray: any[];
        callsPanel: any;
        trisPanel: any;
    } | null>(null);


    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        const ro = new ResizeObserver(entries => {
            for (const entry of entries) {
                const {width, height} = entry.contentRect;
                rendererRef.current?.setSize(width, height);
                if (cameraRef.current) {
                    cameraRef.current.aspect = width / height;
                    cameraRef.current.updateProjectionMatrix();
                }
            }
        });

        ro.observe(container);
        return () => {
            ro.disconnect();
        };
    }, []);

    useEffect(() => {
        if (!containerRef.current) return;

        // ——— INVALIDATE z-up global only once ———
        if (zIsUp) {
            THREE.Object3D.DEFAULT_UP = new THREE.Vector3(0, 0, 1)
        }
        const clock = new THREE.Clock();

        // === Scene ===
        if (!sceneRef.current) {
            const scene = new THREE.Scene();
            scene.background = new THREE.Color("#393939");
            sceneRef.current = scene;
        }
        const scene = sceneRef.current;

        // Create the animation controller
        modelGroupRef.current = new THREE.Group();


        // === Renderer ===
        // Antialias is decided at construction time only; toggling the
        // perfStore.antialias flag requires a reload (the Performance
        // panel surfaces this). Pixel ratio + shadowMap are driven by
        // separate live effects below so flipping them mid-session
        // takes effect without a reload.
        if (!rendererRef.current) {
            const initialPerf = usePerfStore.getState();
            const renderer = new THREE.WebGLRenderer({antialias: initialPerf.antialias});
            renderer.setSize(
                containerRef.current.clientWidth,
                containerRef.current.clientHeight,
            );
            renderer.setPixelRatio(
                Math.min(window.devicePixelRatio, initialPerf.pixelRatioCap),
            );
            renderer.shadowMap.enabled = !initialPerf.disableShadowMap;
            containerRef.current.appendChild(renderer.domElement);
            rendererRef.current = renderer;
        }
        const renderer = rendererRef.current;

        // === Camera ===
        const camera = setupCamera(containerRef.current, zIsUp);
        cameraRef.current = camera;

        // === Orbit Controls ===
        const controls = setupControls(camera, containerRef.current, zIsUp, defaultOrbitController);
        controlsRef.current = controls;

        // === Key Handlers ===
        const removeKeyHandlers = setupCameraControlsHandlers(
            scene,
            camera,
            controls,
        );

        // === Lights ===
        const {updateCameraLight} = setupLights(scene, camera);
        updatelightRef.current = updateCameraLight;

        // === Helpers ===
        const grid_helper = addDynamicGridHelper(scene);

        let gizmo: OrientationGizmo | null = null;
        if (containerRef.current && camera && controls) {
            gizmo = setupGizmo(camera, containerRef.current, controls);
        }

        // === Stats ===
        const {statsArray, callsPanel, trisPanel} = setupStats(containerRef.current);
        statsRef.current = {statsArray, callsPanel, trisPanel};

        // === Model Cache Loader ===
        if (modelUrl) {
            replace_model(modelUrl)
            // delete the B64GLTF from the window object (if it exists)
            if (runtime.b64Gltf()) {
                runtime.clearB64Gltf();
            }
        }
        if (modelGroupRef.current) {
            // If a model is already loaded, add it to the scene
            scene.add(modelGroupRef.current);
        }

        // === Controls events for on-demand render + adaptive DPR ===
        // OrbitControls emits 'change'/'start'/'end'; CameraControls
        // uses 'update'/'controlstart'/'controlend'. Both extend the
        // same EventDispatcher so the listener attachment shape is the
        // same; only the event names differ.
        let interactingTimer: ReturnType<typeof setTimeout> | null = null;
        const applyPixelRatio = (interacting: boolean) => {
            const p = usePerfStore.getState();
            const cap = interacting && p.adaptivePixelRatio ? 1.0 : p.pixelRatioCap;
            renderer.setPixelRatio(Math.min(window.devicePixelRatio, cap));
            requestRender();
        };
        const onControlChange = () => requestRender();
        const onControlStart = () => {
            if (interactingTimer) {
                clearTimeout(interactingTimer);
                interactingTimer = null;
            }
            applyPixelRatio(true);
        };
        const onControlEnd = () => {
            // Wait a beat after the user lets go before pushing DPR back
            // up; damping/momentum keeps moving and we want the cheap
            // pass to cover those frames too.
            if (interactingTimer) clearTimeout(interactingTimer);
            interactingTimer = setTimeout(() => applyPixelRatio(false), 200);
        };
        if (controls instanceof OrbitControls) {
            controls.addEventListener("change", onControlChange);
            controls.addEventListener("start", onControlStart);
            controls.addEventListener("end", onControlEnd);
        } else {
            (controls as any).addEventListener("update", onControlChange);
            (controls as any).addEventListener("controlstart", onControlStart);
            (controls as any).addEventListener("controlend", onControlEnd);
        }

        // === Render loop ===
        const animate = () => {
            requestAnimationFrame(animate);
            // 1) start all stats timers
            statsArray.forEach((s) => s.begin());

            // ``clock.getDelta`` is destructive — calling it more
            // than once per frame eats time from later consumers.
            // Snapshot it here and let everything below share.
            const frameDelta = clock.getDelta();

            // Fix: Always use the current reference, not the captured one
            const animActive = !!(
                animationControllerRef.current && animationControllerRef.current.currentAction
            );
            if (animActive) {
                animationControllerRef.current!.update(frameDelta);
            }

            // Streaming-FEA mode-shape oscillation. No-op when no
            // session is active or play isn't pressed.
            tickFeaAnimation(frameDelta);
            const feaPlaying = useFeaAnimationStore.getState().isPlaying;

            if (controls instanceof OrbitControls) {
                controls.update();
            } else {
                // snip
                controls.update(frameDelta);
            }
            updateCameraLight?.(); // ← Keep the light tracking the camera
            gizmo?.update(); // <-- keep the gizmo synced with the camera

            // On-demand render: skip the expensive renderer.render when
            // nothing visible has changed and no animation is in flight.
            // ``consumeDirty`` clears the global flag set by controls
            // events / requestRender() callers. Stats panels still tick
            // so the FPS overlay reflects real frame cadence.
            const perfNow = usePerfStore.getState();
            const dirty = consumeDirty();
            const shouldRender =
                !perfNow.onDemandRender || dirty || animActive || feaPlaying;
            if (shouldRender) {
                renderer.render(scene, camera);

                // 4) update custom panels from renderer.info — only
                // updates when we actually rendered, otherwise the
                // panel would flatline at zero.
                if (callsPanel) {
                    callsPanel.update(renderer.info.render.calls, 200);
                }
                if (trisPanel) {
                    trisPanel.update(renderer.info.render.triangles, 500_000);
                }
            }

            // 5) end all stats timers
            statsArray.forEach((s) => s.end());

        };
        animate();

        const cleanupResizeHandler = setupResizeHandler(containerRef.current, camera, renderer);
        const cleanupPointerHandler = setupPointerHandler(containerRef.current, camera, scene, renderer, controls);

        return () => {
            // cleanupResizeHandler();
            // cleanupPointerHandler();
            // renderer.dispose();
            // containerRef.current?.removeChild(renderer.domElement);
            if (gizmo && containerRef.current?.contains(gizmo)) {
                containerRef.current.removeChild(gizmo);
            }
            grid_helper.dispose();
            scene.remove(grid_helper);
            removeKeyHandlers?.(); // cleanup key listeners
            if (interactingTimer) clearTimeout(interactingTimer);
            // Detach control-event listeners so they don't pile up on
            // re-mount (zIsUp / defaultOrbitController flips).
            if (controls instanceof OrbitControls) {
                controls.removeEventListener("change", onControlChange);
                controls.removeEventListener("start", onControlStart);
                controls.removeEventListener("end", onControlEnd);
            } else {
                (controls as any).removeEventListener("update", onControlChange);
                (controls as any).removeEventListener("controlstart", onControlStart);
                (controls as any).removeEventListener("controlend", onControlEnd);
            }
            // Clean up model scene from main scene
            // scene.clear();

            // Reset global state
            // sceneRef.current = null;

            // Clean up stats panels
            if (statsArray && statsArray.length > 0) {
                statsArray.forEach((stats) => {
                    containerRef.current?.removeChild(stats.dom);
                });
            }
        };
    }, [defaultOrbitController, zIsUp]);

    // ——— Live-subscribe perf flags that don't need a renderer rebuild ———
    // Shadow map + pixel ratio cap can be flipped on the existing
    // WebGLRenderer without recreating it. Reading them via individual
    // selectors makes the effect rerun only when that specific flag
    // changes; we also call requestRender() so the user sees the
    // change immediately under on-demand mode.
    const disableShadowMap = usePerfStore((s) => s.disableShadowMap);
    const pixelRatioCap = usePerfStore((s) => s.pixelRatioCap);
    useEffect(() => {
        const r = rendererRef.current;
        if (!r) return;
        r.shadowMap.enabled = !disableShadowMap;
        requestRender();
    }, [disableShadowMap]);
    useEffect(() => {
        const r = rendererRef.current;
        if (!r) return;
        r.setPixelRatio(Math.min(window.devicePixelRatio, pixelRatioCap));
        requestRender();
    }, [pixelRatioCap]);

    // ——— Separate effect for toggling performance panels only ———
    useEffect(() => {
        if (!statsRef.current || !containerRef.current) return;
        const {statsArray} = statsRef.current;
        statsArray.forEach(stat => {
            stat.dom.style.display = showPerf ? "block" : "none";
        });
    }, [showPerf]);

    return <div ref={containerRef} className="w-full h-full relative"/>;
};
export default ThreeCanvas;
