import * as THREE from "three";
import {prepareLoadedModel} from "./prepareLoadedModel";
import {useModelState} from "@/state/modelState";
import {useOptionsStore} from "@/state/optionsStore";
import {useAnimationStore} from "@/state/animationStore";
import {animationControllerRef, cameraRef, controlsRef, modelKeyMapRef, sceneRef, simulationDataRef, adaExtensionRef} from "@/state/refs";
import {zoomToAll} from "./setupCameraControlsHandlers";
import {SimulationDataExtensionMetadata} from "@/extensions/design_and_analysis_extension";
import {requestRender} from "@/state/perfStore";
import {FilePurpose} from "@/flatbuffers/base/file-purpose";
import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {mapAnimationTargets} from "@/utils/scene/animations/mapAnimationTargets";
import {loadGLTF} from "./asyncModelLoader";
import {AnimationController} from "@/utils/scene/animations/AnimationController";
import {updateAllPointsSize} from "@/utils/scene/updatePointSizes";
import type {LoadMetricsRecorder} from "@/utils/scene/loadMetrics";
import {fastSceneBox} from "@/utils/scene/boundsFast";
import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";

/** Optional hook to mutate the freshly-loaded gltf scene (typically
 * to inject ``userData["draw_ranges_<meshName>"]`` and
 * ``userData["id_hierarchy"]``) before ``prepareLoadedModel`` walks
 * it. Used by the FEA streaming loader to hydrate per-element draw
 * ranges from the AFEM sidecar — without this hook the FEA mesh
 * would land in the scene as a single-range CustomBatchedMesh and
 * miss the per-element pick + highlight pipeline. */
export type SetupModelPrepareHook = (gltf_scene: THREE.Group) => Promise<void>;

export async function setupModelLoaderAsync(
    modelUrl: string | null,
    translate: boolean = true,
    prepareHook?: SetupModelPrepareHook,
    sourceName?: string,
    // Auth headers for loading directly from the authed REST streaming GET (REST-mode view).
    requestHeaders?: Record<string, string>,
    // Optional admin load-metrics recorder (REST view path). No-op when absent.
    metrics?: LoadMetricsRecorder | null,
): Promise<THREE.Group> {
    if (sceneRef.current == null) {
        console.error("Scene reference is null");
        return new THREE.Group();
    }

    const main_scene = sceneRef.current;

    // 3) prepare & add the model to the scene
    const modelGroup = new THREE.Group();
    if (!modelUrl) return modelGroup;

    // 1) load the GLTF
    const gltf = await loadGLTF(modelUrl, undefined, requestHeaders, metrics);

    const gltf_scene = gltf.scene;
    const animations = gltf.animations;
    const modelStore = useModelState.getState()
    const optionsStore = useOptionsStore.getState()
    const animationStore = useAnimationStore.getState()

    // access the raw JSON
    const ada_ext_data = (gltf as any).parser.json.extensions?.ADA_EXT_data;
    if (ada_ext_data){
        adaExtensionRef.current = ada_ext_data;
        if (ada_ext_data.simulation_objects.length > 0){
            simulationDataRef.current = ada_ext_data.simulation_objects[0];
        }
        // Stash the extension + gltf parser on the group so the caller
        // (update_scene_from_message etc.) can call
        // ``registerLineageFromExtension`` once it knows the source
        // file name. The setLoadedSourceName / registerLoadedSource
        // flips happen AFTER setupModelLoaderAsync returns, so we
        // can't register lineage here without race-conditioning
        // against the wrong (previous) file name.
        gltf_scene.userData.__adaExt = ada_ext_data;
        gltf_scene.userData.__adaGltf = gltf;
    }
    animationControllerRef.current = new AnimationController(main_scene);
    // Handle animations - clear previous state first
    if (animations.length > 0) {
        // Set the hasAnimation flag to true in the store
        animationStore.setHasAnimation(true);

        // Clear previous animations completely
        animationControllerRef.current?.clear();

        // Set the mesh map for the new animations
        animationControllerRef.current?.setMeshMap(mapAnimationTargets(gltf));

        // Add animations to the controller
        animations.forEach((animation) => {
            animationControllerRef.current?.addAnimation(animation);
        });

        // Reset to no animation state - don't call setCurrentAnimation yet
        // We'll do this after the model is fully loaded and added to scene
    } else {
        animationStore.setHasAnimation(false);
        // Clear controller even if no animations to ensure clean state
        animationControllerRef.current?.clear();
    }

    // create a unique hash string
    const model_hash = gltf_scene.name + "_" + gltf_scene.uuid;

    if (prepareHook) {
        await prepareHook(gltf_scene);
    }

    await prepareLoadedModel({gltf_scene: gltf_scene, hash: model_hash});
    // once userData is on the scene:
    const rawUD = (gltf_scene.userData ?? {}) as Record<
        string,
        any
    >;

    // delegate all the caching to our helper (sourceName -> the tree root label)
    await cacheAndBuildTree(model_hash, rawUD, sourceName);

    if (modelStore.translation && translate) {
        console.log("Model already translated");
        gltf_scene.position.add(modelStore.translation);
    } else {
        // Union of per-geometry boundingBoxes (set cheaply in
        // prepareLoadedModel via fastComputeBounds) — avoids setFromObject's
        // per-vertex iteration on large models.
        const boundingBox = fastSceneBox(gltf_scene);
        modelStore.setBoundingBox(boundingBox);

        if (!optionsStore.lockTranslation) {
            const center = boundingBox.getCenter(new THREE.Vector3());
            const translation = center.clone().multiplyScalar(-1);
            if (modelStore.zIsUp) {
                const minZ = boundingBox.min.z;
                const bheight = boundingBox.max.z - minZ;
                translation.z = -minZ + bheight * 0.05;
            } else {
                const minY = boundingBox.min.y;
                const bheight = boundingBox.max.y - minY;
                translation.y = -minY + bheight * 0.05;
            }

            gltf_scene.position.add(translation);
            modelStore.setTranslation(translation);
        }
    }


    modelGroup.add(gltf_scene);

    main_scene.add(modelGroup);
    // Mesh/material/tree build is done; the model is now in the scene.
    // finalize() captures GPU/first-render via a post-add rAF, gathers
    // payload + device + profile, and posts the load-metrics row.
    metrics?.markPrepareDone();
    metrics?.finalize(modelGroup, gltf as unknown as {parser?: {json?: any}});
    // The render loop only fires on OrbitControls 'change' events
    // or explicit ``requestRender()`` calls (ThreeCanvas.tsx:158).
    // Without this kick the freshly-added model only paints once the
    // user rotates / pans the camera.
    requestRender();

    // Ensure point sizes and sizing mode are applied after the model is in the scene,
    // so points are visible immediately without needing the Options panel.
    try {
        const ps = optionsStore.pointSize ?? 0.01;
        const abs = (optionsStore as any).pointSizeAbsolute ?? true;
        updateAllPointsSize(ps, abs);
    } catch {}

    if (!modelKeyMapRef.current) {
        modelKeyMapRef.current = new Map<string, THREE.Object3D>();
    }
    modelKeyMapRef.current.set(model_hash, modelGroup);

    if (animations.length > 0) {
        // Set the hasAnimation flag to true in the store
        animationStore.setHasAnimation(true);
    }

    // Adapt near/far clipping to the loaded model's size so small models can be zoomed into without
    // near-plane clipping — independent of autoFit (zoomToAll re-applies it, but this covers the
    // autoFit-off case too). Uses the model bounding box captured above / in the store.
    {
        const cam = cameraRef.current;
        const ctl = controlsRef.current;
        const box = useModelState.getState().boundingBox; // fresh — setBoundingBox ran above
        if (cam && box && !box.isEmpty()) {
            const radius = box.getBoundingSphere(new THREE.Sphere()).radius;
            applyAdaptiveClipping(cam as THREE.PerspectiveCamera, ctl, radius);
        }
    }

    // Auto fit-to-all after the model is in the scene (scene-config toggle, default on) —
    // frames a freshly loaded model, and each geom cycled through in gallery mode, without a
    // manual Shift+A. Deferred a frame so the just-added meshes' world bounds are current.
    if (optionsStore.autoFit) {
        const cam = cameraRef.current;
        const ctl = controlsRef.current;
        const scn = sceneRef.current;
        if (cam && ctl && scn) {
            requestAnimationFrame(() => zoomToAll(scn, cam as THREE.PerspectiveCamera, ctl));
        }
    }
    return modelGroup;
}
