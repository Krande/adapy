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
import {rotateGridHelper} from "./sceneHelpers/rotateGridHelper";
import {addDynamicGridHelper} from "./sceneHelpers/addDynamicGridHelper";
import {setupGizmo} from "./sceneHelpers/setupGizmo";
import {setupStats} from "./sceneHelpers/setupStats";
import {setupModelLoader} from "./sceneHelpers/setupModelLoader";
import {setupResizeHandler} from "./sceneHelpers/setupResizeHandler";
import {setupPointerHandler} from "./sceneHelpers/setupPointerHandler";
import {cameraRef, controlsRef, rendererRef} from "../../state/refs";

const ThreeCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const {modelUrl, setScene, zIsUp} = useModelStore();
    const {showPerf} = useOptionsStore();

    useEffect(() => {
        if (!containerRef.current) return;
        if (zIsUp) {
            THREE.Object3D.DEFAULT_UP = new THREE.Vector3(0,0,1)
        }
        // === Scene ===
        const scene = new THREE.Scene();
        setScene(scene);
        scene.background = new THREE.Color("#393939");


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
        const controls = setupControls(camera, containerRef.current, zIsUp);
        controlsRef.current = controls;

        // === Key Handlers ===
        const removeKeyHandlers = setupCameraControlsHandlers(
            scene,
            camera,
            controls,
        );

        // === Lights ===
        const {updateCameraLight} = setupLights(scene, camera);

        // === Helpers ===
        addDynamicGridHelper(scene);

        let gizmo: OrientationGizmo | null = null;
        if (containerRef.current && camera && controls) {
            gizmo = setupGizmo(camera, containerRef.current, controls);
        }

        // === Stats ===
        const statsArray = containerRef.current ? setupStats(containerRef.current, showPerf) : [];

        // === Model Loader ===
        setupModelLoader(scene, modelUrl);

        // === Render loop ===
        let previousTime = performance.now();
        const animate = () => {
            requestAnimationFrame(animate);

            const currentTime = performance.now();
            const delta = (currentTime - previousTime) / 1000;
            previousTime = currentTime;

            const {action} = useAnimationStore.getState();
            if (action) {
                const mixer = action.getMixer();
                mixer.update(delta);
                useAnimationStore.getState().setCurrentKey(action.time);
            }
            controls.update();
            updateCameraLight?.(); // ← Keep the light tracking the camera
            gizmo?.update(); // <-- keep the gizmo synced with the camera
            renderer.render(scene, camera);

            // Update each stats panel (FPS, MS, Memory)
            statsArray.forEach((stats) => {
                stats.begin();
                stats.end();
            });

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
    }, [modelUrl, showPerf]);

    // === Coordinate System Switcher ===
    useEffect(() => {
        const scene = useModelStore.getState().scene;
        const container = containerRef.current;
        const camera = cameraRef.current;
        const oldControls = controlsRef.current;

        if (!scene || !container || !camera) return;

        // 1. Dispose old controls
        if (oldControls) {
            oldControls.dispose();
        }

        // 2. Update camera up
        const up = zIsUp ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
        camera.up.copy(up);
        camera.updateProjectionMatrix();

        // 3. Create fresh controls
        controlsRef.current = setupControls(camera, container, zIsUp);

        // 4. Rotate grid helper
        rotateGridHelper(scene, zIsUp);
    }, [zIsUp]);

    return <div ref={containerRef} className="w-full h-full relative"/>;
};
export default ThreeCanvas;
