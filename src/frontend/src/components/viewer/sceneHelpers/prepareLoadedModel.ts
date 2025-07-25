// sceneHelpers/prepareLoadedModel.ts
import * as THREE from "three";
import {convert_to_custom_batch_mesh} from "../../../utils/scene/convert_to_custom_batch_mesh";
import {replaceBlackMaterials} from "../../../utils/scene/assignDefaultMaterial";
import {useModelState} from "../../../state/modelState";
import {useOptionsStore} from "../../../state/optionsStore";
import {adaExtensionRef, rendererRef} from "../../../state/refs";
import {useAnimationStore} from "../../../state/animationStore";
import {assignMorphToEdgeAlso} from "../../../utils/scene/animations/assignMorphToEdgeAlso";
import {DesignDataExtension, SimulationDataExtensionMetadata} from "../../../extensions/design_and_analysis_extension";

interface PrepareLoadedModelParams {
    gltf_scene: THREE.Object3D;
    hash: string
}

async function get_ada_ext_simulation_data(mesh: THREE.Mesh): Promise<SimulationDataExtensionMetadata | null> {
    const ada_ext = adaExtensionRef.current;
    if (!ada_ext){
        return null;
    }

    // Use for...of instead of for...in to iterate over array elements
    if (ada_ext.simulation_objects) {
        for (const sim_obj of ada_ext.simulation_objects) {
            const sim_face_node_ref = sim_obj.node_references?.faces;
            if (mesh.name == sim_face_node_ref){
                return sim_obj;
            }
            if (mesh.userData?.name == sim_face_node_ref){
                return sim_obj;
            }
        }
    }
    return null
}

async function get_ada_ext_design_data(mesh: THREE.Mesh): Promise<DesignDataExtension | null> {
    const ada_ext = adaExtensionRef.current;
    if (!ada_ext){
        return null;
    }

    if (ada_ext.design_objects) {
        for (const design_obj of ada_ext.design_objects) {
            const design_face_node_ref = design_obj.node_references?.faces;
            if (mesh.name == design_face_node_ref){
                return design_obj;
            }
            if (mesh.userData?.name == design_face_node_ref){
                return design_obj;
            }
        }
    }

    return null;
}

export async function prepareLoadedModel({gltf_scene, hash}: PrepareLoadedModelParams): Promise<void> {
    const modelStore = useModelState.getState()
    const optionsStore = useOptionsStore.getState()

    // we'll collect all edge geometries here
    const meshesToReplace: { original: THREE.Mesh; parent: THREE.Object3D }[] = [];

    gltf_scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
            meshesToReplace.push({original: object, parent: object.parent!});
        } else if (object instanceof THREE.LineSegments || object instanceof THREE.Points) {
            object.layers.set(1);
        }
    });


    for (const {original, parent} of meshesToReplace) {
        const meshName = original.name;
        let drawRangesData = gltf_scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;
        const node_id = original.userData?.node_id

        // if length is 0, we don't need to convert
        if (!drawRangesData && node_id){
            drawRangesData = gltf_scene.userData[`draw_ranges_node${node_id}`] as Record<string, [number, number]>;
        }
        if (!drawRangesData) {
            console.warn(`No draw ranges found for mesh: ${meshName}`);
        }

        const drawRanges = new Map<string, [number, number]>();
        if (drawRangesData) {
            for (const [rangeId, [start, count]] of Object.entries(drawRangesData)) {
                drawRanges.set(rangeId, [start, count]);
            }
        }

        const ada_ext_design = await get_ada_ext_design_data(original);
        const ada_ext_sim = await get_ada_ext_simulation_data(original);

        let is_design = false;
        if (ada_ext_sim == null && ada_ext_design == null){
            is_design = true;
        }
        else if (ada_ext_sim != null){
            is_design = false
        } else if (ada_ext_design != null){
            is_design = true
        }
        const ada_ext_data = is_design ? ada_ext_design : ada_ext_sim;

        const customMesh = convert_to_custom_batch_mesh(original, drawRanges, hash, is_design, ada_ext_data);

        if (optionsStore.showEdges && drawRanges.size && is_design) {
            if (rendererRef.current)
                parent.add(customMesh.getEdgeOverlay(rendererRef.current));
        }

        parent.add(customMesh);
        if (useAnimationStore.getState().hasAnimation && !is_design) {
            const line_geo = original.children[0] as THREE.LineSegments;
            try {
                assignMorphToEdgeAlso(customMesh, line_geo);
            } catch (e) {
                console.error("Error assigning morph to edge:", e);
            }

            parent.add(line_geo)
        }
        parent.remove(original);
    }

    replaceBlackMaterials(gltf_scene);
}
