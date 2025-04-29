// ThreeCanvas.tsx
import React, {useEffect, useRef} from "react";
import * as THREE from "three";
import {useModelStore} from "../../state/modelStore";
import {useOptionsStore} from "../../state/optionsStore";
import {useAnimationStore} from "../../state/animationStore";
import {OrientationGizmo} from "./sceneHelpers/OrientationGizmo";
import {setupCameraControlsHandlers} from "./sceneHelpers/setupCameraControlsHandlers";
import {setupCamera} from "./sceneHelpers/setupCamera";
import {setupControls} from "./sceneHelpers/setupControls";
import {setupLights} from "./sceneHelpers/setupLights";
import {addDynamicGridHelper} from "./sceneHelpers/addDynamicGridHelper";
import {setupGizmo} from "./sceneHelpers/setupGizmo";
import {setupStats} from "./sceneHelpers/setupStats";
import {setupModelLoader} from "./sceneHelpers/setupModelLoader";
import {setupResizeHandler} from "./sceneHelpers/setupResizeHandler";
import {setupPointerHandler} from "./sceneHelpers/setupPointerHandler";
import {cameraRef, controlsRef, rendererRef, sceneRef, updatelightRef} from "../../state/refs";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

const ThreeCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const {modelUrl, setScene, zIsUp, defaultOrbitController} = useModelStore();
    const {action, setCurrentKey} = useAnimationStore();
    const {showPerf} = useOptionsStore();
    const modelGroupRef = useRef<THREE.Group | null>(null); // <-- store loaded model separately

    useEffect(() => {
        if (!containerRef.current) return;
        if (zIsUp) {
            THREE.Object3D.DEFAULT_UP = new THREE.Vector3(0, 0, 1)
        }
        const clock = new THREE.Clock();

        // === Scene ===
        const scene = new THREE.Scene();
        scene.background = new THREE.Color("#393939");
        sceneRef.current = scene;
        setScene(scene);

        // === Renderer ===
        const renderer = new THREE.WebGLRenderer({antialias: true});
        renderer.setSize(
            containerRef.current.clientWidth,
            containerRef.current.clientHeight,
        );
        renderer.shadowMap.enabled = true;
        containerRef.current.appendChild(renderer.domElement);
        rendererRef.current = renderer;

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
        addDynamicGridHelper(scene);

        let gizmo: OrientationGizmo | null = null;
        if (containerRef.current && camera && controls) {
            gizmo = setupGizmo(camera, containerRef.current, controls);
        }

        // === Stats ===
        const {statsArray, callsPanel, trisPanel} = setupStats(
            containerRef.current,
            showPerf,
        );

        // === Model Loader ===
        if (modelUrl) {
            modelGroupRef.current = setupModelLoader(scene, modelUrl);
        } else if (modelGroupRef.current) {
            // If a model is already loaded, add it to the scene
            scene.add(modelGroupRef.current);
        }

        // === Render loop ===
        let prevTime = performance.now();

        const animate = () => {
            requestAnimationFrame(animate);
            // 1) start all stats timers
            statsArray.forEach((s) => s.begin());

            if (action) {
                const now = performance.now();
                const dt = (now - prevTime) / 1000;
                prevTime = now;
                action.getMixer().update(dt);
                setCurrentKey(action.time);
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
            cleanupResizeHandler();
            cleanupPointerHandler();
            renderer.dispose();
            containerRef.current?.removeChild(renderer.domElement);
            if (gizmo && containerRef.current?.contains(gizmo)) {
                containerRef.current.removeChild(gizmo);
            }
            removeKeyHandlers?.(); // cleanup key listeners
            // Clean up model scene from main scene
            scene.clear();

            // Reset global state
            useModelStore.getState().setScene(null);
            useModelStore.getState().setRaycaster(null);

            // Clean up stats panels
            if (statsArray && statsArray.length > 0) {
                statsArray.forEach((stats) => {
                    containerRef.current?.removeChild(stats.dom);
                });
            }
        };
    }, [modelUrl, showPerf, defaultOrbitController, zIsUp]);

    return <div ref={containerRef} className="w-full h-full relative"/>;
};
export default ThreeCanvas;
