// sceneHelpers/prepareLoadedModel.ts
import * as THREE from "three";
import {convert_to_custom_batch_mesh} from "../../../utils/scene/convert_to_custom_batch_mesh";
import {replaceBlackMaterials} from "../../../utils/scene/assignDefaultMaterial";
import {useModelState} from "../../../state/modelState";
import {useOptionsStore} from "../../../state/optionsStore";
import {adaExtensionRef, rendererRef} from "../../../state/refs";
import {useAnimationStore} from "../../../state/animationStore";
import {assignMorphToEdgeAlso} from "../../../utils/scene/animations/assignMorphToEdgeAlso";
import {assignMorphToPointsAlso} from "../../../utils/scene/animations/assignMorphToPointsAlso";
import {DesignDataExtension, SimulationDataExtensionMetadata} from "../../../extensions/design_and_analysis_extension";
import {applySphericalImpostor} from "../../../utils/scene/pointsImpostor";
import {updateAllPointsSize} from "../../../utils/scene/updatePointSizes";
import {gpuPointPicker} from "../../../utils/mesh_select/GpuPointPicker";

interface PrepareLoadedModelParams {
    gltf_scene: THREE.Object3D;
    hash: string
}

async function get_ada_ext_simulation_data(mesh: THREE.Mesh): Promise<SimulationDataExtensionMetadata | null> {
    const ada_ext = adaExtensionRef.current;
    if (!ada_ext) {
        return null;
    }

    // Use for...of instead of for...in to iterate over array elements
    if (ada_ext.simulation_objects) {
        for (const sim_obj of ada_ext.simulation_objects) {
            const sim_face_node_ref = sim_obj.node_references?.faces;
            if (mesh.name == sim_face_node_ref) {
                return sim_obj;
            }
            if (mesh.userData?.name == sim_face_node_ref) {
                return sim_obj;
            }
        }
    }
    return null
}

async function get_ada_ext_design_data(mesh: THREE.Mesh): Promise<DesignDataExtension | null> {
    const ada_ext = adaExtensionRef.current;
    if (!ada_ext) {
        return null;
    }

    if (ada_ext.design_objects) {
        for (const design_obj of ada_ext.design_objects) {
            const design_face_node_ref = design_obj.node_references?.faces;
            if (mesh.name == design_face_node_ref) {
                return design_obj;
            }
            if (mesh.userData?.name == design_face_node_ref) {
                return design_obj;
            }
        }
    }

    return null;
}

export async function prepareLoadedModel({gltf_scene, hash}: PrepareLoadedModelParams): Promise<void> {
    const optionsStore = useOptionsStore.getState()

    // we'll collect all edge geometries here
    const meshesToReplace: { original: THREE.Mesh; parent: THREE.Object3D }[] = [];

    gltf_scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
            meshesToReplace.push({original: object, parent: object.parent!});
        } else if (object instanceof THREE.LineSegments) {
            // Keep edges in non-pickable layer 1, but allow Points to remain pickable on default layer 0
            object.layers.set(1);
        } else if (object instanceof THREE.Points) {
            // Convert to spherical impostor material and initialize size
            const ps = optionsStore.pointSize ?? 5.0;
            try {
                applySphericalImpostor(object, ps);
                try { gpuPointPicker.registerPoints(object); } catch (_) {}
            } catch (e) {
                // Fallback: just apply size to existing material
                const mat = object.material as THREE.Material | THREE.Material[];
                const applySize = (m: THREE.Material) => {
                    if ((m as any).isPointsMaterial) {
                        const pm = m as THREE.PointsMaterial;
                        pm.size = ps;
                        pm.sizeAttenuation = true;
                        pm.needsUpdate = true;
                    } else if ((m as any).isShaderMaterial) {
                        const sm = m as THREE.ShaderMaterial & { uniforms?: any };
                        if (sm.uniforms && sm.uniforms.pointSize) {
                            sm.uniforms.pointSize.value = ps;
                            sm.needsUpdate = true;
                        }
                    }
                };
                if (Array.isArray(mat)) mat.forEach(applySize); else if (mat) applySize(mat);
            }
            object.userData["unique_hash"] = hash;
        }
    });


    for (const {original, parent} of meshesToReplace) {
        const meshName = original.name;
        let drawRangesData = gltf_scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;
        const node_id = original.userData?.node_id

        // if length is 0, we don't need to convert
        if (!drawRangesData && node_id) {
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
        if (ada_ext_sim == null && ada_ext_design == null) {
            is_design = true;
        } else if (ada_ext_sim != null) {
            is_design = false
        } else if (ada_ext_design != null) {
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
            // Ensure custom mesh inherits morph target state for raycasting and rendering
            const geom = customMesh.geometry as THREE.BufferGeometry;
            const hasMorphs = !!geom.morphAttributes && Array.isArray(geom.morphAttributes.position) && geom.morphAttributes.position.length > 0;
            if (hasMorphs) {
                // Copy morph influences/dictionary from original
                (customMesh as any).morphTargetInfluences = (original as any).morphTargetInfluences;
                (customMesh as any).morphTargetDictionary = (original as any).morphTargetDictionary;
                // Enable morph targets on material(s)
                if (Array.isArray(customMesh.material)) {
                    customMesh.material.forEach((m: any) => { if (m && 'morphTargets' in m) { m.morphTargets = true; m.needsUpdate = true; } });
                } else {
                    const m: any = customMesh.material as any;
                    if (m && 'morphTargets' in m) { m.morphTargets = true; m.needsUpdate = true; }
                }
            }
            // Handle edge overlay (LineSegments) morph following
            const lineChildren = original.children.filter((c): c is THREE.LineSegments => c instanceof THREE.LineSegments);
            for (const line_geo of lineChildren) {
                try {
                    assignMorphToEdgeAlso(customMesh, line_geo);
                } catch (e) {
                    console.error("Error assigning morph to edge:", e);
                }
                parent.add(line_geo);
            }

            // Handle Points children morph following
            const pointChildren = original.children.filter((c): c is THREE.Points => c instanceof THREE.Points);
            for (const pts of pointChildren) {
                try {
                    // Ensure spherical impostor with morph support is applied
                    const ps = optionsStore.pointSize ?? 5.0;
                    applySphericalImpostor(pts, ps);
                } catch (e) {
                    // ignore, fallback already applied in initial traverse
                }
                try { gpuPointPicker.registerPoints(pts); } catch (_) {}
                try {
                    assignMorphToPointsAlso(customMesh, pts);
                } catch (e) {
                    console.error("Error assigning morph to points:", e);
                }
                parent.add(pts);
            }
        } else {
            // No animation loaded: keep Points children as-is (zero deformation baseline)
            const pointChildren = original.children.filter((c): c is THREE.Points => c instanceof THREE.Points);
            for (const pts of pointChildren) {
                try {
                    // Ensure they have impostor material applied (already applied during traverse)
                    const ps = optionsStore.pointSize ?? 5.0;
                    applySphericalImpostor(pts, ps);
                } catch (e) {
                    // ignore if already set
                }
                try { gpuPointPicker.registerPoints(pts); } catch (_) {}
                parent.add(pts);
            }
        }
        parent.remove(original);

    }

    replaceBlackMaterials(gltf_scene);
}
