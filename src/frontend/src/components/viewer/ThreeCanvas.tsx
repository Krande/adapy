// VanillaCanvas.tsx
import React, {useEffect, useRef} from "react";
import Stats from "three/examples/jsm/libs/stats.module";
import * as THREE from "three";
import {useModelStore} from "../../state/modelStore";
import {useOptionsStore} from "../../state/optionsStore";
import {handleClickEmptySpace} from "../../utils/mesh_select/handleClickEmptySpace";
import {initScene} from "./sceneHelpers/initScene";
import {prepareLoadedModel} from "./sceneHelpers/prepareLoadedModel";
import {useTreeViewStore} from "../../state/treeViewStore";
import {useAnimationStore} from "../../state/animationStore";
import {addCameraLightWithTracking} from "./sceneHelpers/addCameraLight";
import {handleClickMeshVanilla} from "../../utils/mesh_select/handleClickMeshVanilla";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {OrientationGizmo} from "./sceneHelpers/OrientationGizmo";
import {addOrientationGizmo} from "./sceneHelpers/addOrientationGizmo";
import {initAnimationEffects} from "./sceneHelpers/initAnimationEffects";
import {setupCameraControlsHandlers} from "./sceneHelpers/setupCameraControlsHandlers";
import {applyCoordinateSystem} from "./sceneHelpers/useCoordinateSystemSwitcher";

const ThreeCanvas: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const {modelUrl, setScene, zIsUp} = useModelStore();
    const {showPerf} = useOptionsStore();

    const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
    const controlsRef = useRef<OrbitControls | null>(null);
    const rendererRef = useRef<THREE.WebGLRenderer | null>(null);


    useEffect(() => {
        if (!containerRef.current) return;

        // === Scene ===
        const scene = new THREE.Scene();
        setScene(scene);
        scene.background = new THREE.Color("#393939");

        // === Camera ===
        const camera = new THREE.PerspectiveCamera(
            60,
            containerRef.current.clientWidth / containerRef.current.clientHeight,
            0.1,
            10000,
        );

        camera.position.set(-5, 5, 5);
        // Enable both layers for the camera
        camera.layers.enable(0);
        camera.layers.enable(1);

        let updateCameraLight: (() => void) | null = null;
        updateCameraLight = addCameraLightWithTracking(camera, scene);
        // === Renderer ===
        const renderer = new THREE.WebGLRenderer({antialias: true});
        renderer.setSize(
            containerRef.current.clientWidth,
            containerRef.current.clientHeight,
        );
        renderer.shadowMap.enabled = true;
        containerRef.current.appendChild(renderer.domElement);
        const up = zIsUp ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
        camera.up.copy(up);
        // === Controls ===
        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = false;

        const removeKeyHandlers = setupCameraControlsHandlers(
            scene,
            camera,
            controls,
        );
        // === Lights ===
        const ambientLight = new THREE.AmbientLight(0xffffff, Math.PI / 2);
        scene.add(ambientLight);
        // Add CameraLight logic as needed

        // === Helpers ===
        // Add DynamicGridHelper and OrientationGizmo here
        initScene(scene, camera);
        let gizmo: OrientationGizmo | null = null;
        if (containerRef.current) {
            gizmo = addOrientationGizmo(camera, containerRef.current);
            // @ts-ignore
            gizmo.onAxisSelected = ({axis, direction}) => {
                // Update the controls to look toward the clicked direction
                const distance = camera.position.length(); // Maintain same distance from target

                // Move the camera along the selected axis direction
                camera.position.copy(direction.clone().multiplyScalar(distance));
                controls.target.set(0, 0, 0); // Look at the center (origin)
                controls.update();
            };
        }
        cameraRef.current = camera;
        controlsRef.current = controls;
        rendererRef.current = renderer;

        applyCoordinateSystem(
            cameraRef.current,
            controlsRef.current,
            scene,
            zIsUp,
        );


        // === Stats ===
        let stats: Stats | null = null;
        if (showPerf) {
            stats = new Stats();
            stats.showPanel(0); // 0: fps, 1: ms, 2: mb, 3+: custom
            stats.showPanel( 1 );
            stats.showPanel( 2 );
            containerRef.current.appendChild(stats.dom);
            Object.assign(stats.dom.style, {
                position: "absolute",
                top: "0px",
                right: "0px",
                left: "auto", // override default left
                zIndex: "20",
            });
        }

        // === Load Model ===
        if (modelUrl) {
            // Define a loader function that can take `scene_action`/`scene_action_arg`
            const loader = new GLTFLoader();
            loader.load(
                modelUrl,
                (gltf) => {
                    const loadedScene = gltf.scene;

                    const modelGroup = new THREE.Group();
                    modelGroup.add(loadedScene);
                    scene.add(modelGroup);

                    prepareLoadedModel({
                        scene: loadedScene,
                        modelStore: useModelStore.getState(),
                        optionsStore: useOptionsStore.getState(),
                        treeViewStore: useTreeViewStore.getState(),
                        animationStore: useAnimationStore.getState(),
                    });
                    // === Animations ===
                    const animations = gltf.animations;
                    let mixer: THREE.AnimationMixer | null = null;

                    if (animations.length > 0) {
                        mixer = initAnimationEffects(animations, loadedScene);
                        let a0 = animations[0];
                        const action = mixer.clipAction(a0);
                        action.play();
                        useAnimationStore.getState().setAction(action);
                        useAnimationStore.getState().setSelectedAnimation(a0.name);
                        useAnimationStore.getState().setCurrentKey(0);
                    }
                },
                undefined,
                (error) => {
                    console.error("Error loading model:", error);
                },
            );
        }

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
            stats?.update();
        };
        animate();

        // === Resize ===
        const handleResize = () => {
            if (!containerRef.current) return;
            camera.aspect =
                containerRef.current.clientWidth / containerRef.current.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(
                containerRef.current.clientWidth,
                containerRef.current.clientHeight,
            );
        };
        window.addEventListener("resize", handleResize);

        // === Click Handling ===
        const onClick = (event: MouseEvent) => {
            const rect = renderer.domElement.getBoundingClientRect();
            const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
            const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
            const pointer = new THREE.Vector2(x, y);
            const raycaster = new THREE.Raycaster();
            // Set raycaster to only detect objects on layer 0
            raycaster.layers.set(0);
            raycaster.layers.disable(1);
            raycaster.setFromCamera(pointer, camera);
            const intersects = raycaster.intersectObjects(scene.children, true);
            if (intersects.length === 0) {
                handleClickEmptySpace(event);
            } else {
                // Assuming your `handleClickMesh` expects the intersected mesh or event
                handleClickMeshVanilla(intersects[0], event); // or pass more if needed
            }
        };
        renderer.domElement.addEventListener("pointerdown", onClick);

        return () => {
            window.removeEventListener("resize", handleResize);
            renderer.domElement.removeEventListener("pointerdown", onClick);

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

            stats && containerRef.current?.removeChild(stats.dom);
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

        // 2. Set camera.up correctly before creating new controls
        if (zIsUp) {
            camera.up.set(0, 0, 1);
        } else {
            camera.up.set(0, 1, 0);
        }
        camera.updateProjectionMatrix();

        // 3. Create fresh controls
        const newControls = new OrbitControls(camera, container.querySelector("canvas")!);
        newControls.enableDamping = false;
        newControls.screenSpacePanning = !zIsUp;
        newControls.target.set(0, 0, 0);
        newControls.update();

        controlsRef.current = newControls;

        // 4. Rotate grid if exists
        const grid = scene.children.find(
            (child) => child instanceof THREE.GridHelper
        ) as THREE.GridHelper | undefined;

        if (grid) {
            grid.rotation.set(zIsUp ? Math.PI / 2 : 0, 0, 0);
        }
    }, [zIsUp]);
    return <div ref={containerRef} className="w-full h-full relative"/>;
};
export default ThreeCanvas;
