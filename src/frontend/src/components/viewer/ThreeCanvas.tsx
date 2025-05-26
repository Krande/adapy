// ThreeCanvas.tsx
import React, {useEffect, useRef} from "react";
import * as THREE from "three";
import {useModelState} from "../../state/modelState";
import {useOptionsStore} from "../../state/optionsStore";
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
import {animationControllerRef, cameraRef, controlsRef, rendererRef, sceneRef, updatelightRef} from "../../state/refs";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {AnimationController} from "../../utils/scene/animations/AnimationController";


const ThreeCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const {zIsUp, defaultOrbitController} = useModelState();
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
        if (!animationControllerRef.current) {
            modelGroupRef.current = new THREE.Group();
            animationControllerRef.current = new AnimationController(scene);
        }
        const animation_controls = animationControllerRef.current

        // === Renderer ===
        if (!rendererRef.current) {
            const renderer = new THREE.WebGLRenderer({antialias: true});
            renderer.setSize(
                containerRef.current.clientWidth,
                containerRef.current.clientHeight,
            );
            renderer.shadowMap.enabled = true;
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
        if (modelGroupRef.current) {
            // If a model is already loaded, add it to the scene
            scene.add(modelGroupRef.current);
        }

        // === Render loop ===
        const animate = () => {
            requestAnimationFrame(animate);
            // 1) start all stats timers
            statsArray.forEach((s) => s.begin());

            if (animation_controls && animation_controls.currentAction) {
                const deltaTime = clock.getDelta();
                animation_controls.update(deltaTime);
            }

            if (controls instanceof OrbitControls) {
                controls.update();
            } else {
                // snip
                const frame_delta = clock.getDelta();
                controls.update(frame_delta);
            }
            updateCameraLight?.(); // ← Keep the light tracking the camera
            gizmo?.update(); // <-- keep the gizmo synced with the camera
            renderer.render(scene, camera);

            // 4) update custom panels from renderer.info
            if (callsPanel) {
                callsPanel.update(renderer.info.render.calls, 200);
            }
            if (trisPanel) {
                trisPanel.update(renderer.info.render.triangles, 500_000);
            }

            // 5) end all stats timers
            statsArray.forEach((s) => s.end());

        };
        animate();

        const cleanupResizeHandler = setupResizeHandler(containerRef.current, camera, renderer);
        const cleanupPointerHandler = setupPointerHandler(containerRef.current, camera, scene, renderer);

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
