import * as THREE from "three";
import {modelStore} from "../state/model_worker/modelStore";
import {useSelectedObjectStore} from '../state/useSelectedObjectStore';
import {modelKeyMapRef} from '../state/refs';
import {CustomBatchedMesh} from './mesh_select/CustomBatchedMesh';
import {enablePointSelectionMask} from "./scene/pointsImpostor";
import {queryPointRangeByRangeId} from "./mesh_select/queryMeshDrawRange";
import {selectedMaterial} from "./default_materials";

export async function selectGroupMembers(
    model_key: string,
    memberNames: string[],
    fe_object_type?: 'node' | 'element'
) {
    const selectedObjectStore = useSelectedObjectStore.getState();

    // Clear selection if no members or invalid model key
    if (!memberNames || memberNames.length === 0 || !model_key) {
        selectedObjectStore.clearSelectedObjects();
        return;
    }

    if (!modelStore) {
        console.error('No model store or active model key available');
        return;
    }

    try {
        // Get draw ranges for all group members from the worker
        const meshRangePairs = await modelStore.getDrawRangesByMemberNames(
            model_key,
            memberNames
        );

        if (meshRangePairs.length === 0) {
            console.warn('No draw ranges found for group members:', memberNames);
            selectedObjectStore.clearSelectedObjects();
            return;
        }

        const modelKeyMap = modelKeyMapRef.current;
        if (!modelKeyMap) {
            console.error('No model key map available');
            return;
        }
        const root = modelKeyMap.get(model_key);
        if (!root) {
            console.error(`Mesh object not found for model key: ${model_key}`);
            return;
        }

        if (fe_object_type === 'node') {
            // Node groups refer to Points. Group rangeIds by meshName, find Points, select and mask indices.
            const rangesByMesh = new Map<string, string[]>();
            for (const [meshName, rangeId] of meshRangePairs) {
                if (!rangesByMesh.has(meshName)) rangesByMesh.set(meshName, []);
                rangesByMesh.get(meshName)!.push(rangeId);
            }

            // Map meshName -> Points[]
            const pointsByName = new Map<string, THREE.Points[]>();
            root.traverse((obj: THREE.Object3D) => {
                if ((obj as any).isPoints) {
                    const pts = obj as THREE.Points;
                    const name = pts.name;
                    if (rangesByMesh.has(name)) {
                        const arr = pointsByName.get(name) ?? [];
                        arr.push(pts);
                        pointsByName.set(name, arr);
                    }
                }
            });

            // Clear current selection
            selectedObjectStore.clearSelectedObjects();

            // Add ranges to selection map for each matching Points
            for (const [meshName, rangeIds] of rangesByMesh) {
                const ptsList = pointsByName.get(meshName) || [];
                for (const pts of ptsList) {
                    for (const rid of rangeIds) {
                        selectedObjectStore.addSelectedObject(pts as unknown as CustomBatchedMesh, rid);
                    }
                }
            }

            // Apply shader masks for all currently selected Points (similar to handleClickPoints)
            const selected = useSelectedObjectStore.getState().selectedObjects;
            for (const [obj, selectedRanges] of selected) {
                const pointsObj = obj as any;
                if (!pointsObj || !pointsObj.isPoints) continue;
                const key: string | undefined = pointsObj.userData ? pointsObj.userData['unique_hash'] : undefined;
                if (!key) continue;
                const meshName = (pointsObj as THREE.Points).name;
                const indices: number[] = [];
                for (const rid of selectedRanges) {
                    const range = await queryPointRangeByRangeId(key, meshName, rid);
                    if (!range) continue;
                    const [start, length] = range;
                    const end = start + length;
                    for (let i = start; i < end; i++) indices.push(i);
                }
                // enable mask and write indices to 'sel'
                enablePointSelectionMask(pointsObj as THREE.Points, selectedMaterial.color);
                const geom = (pointsObj as THREE.Points).geometry as THREE.BufferGeometry;
                const posAttr = geom.getAttribute('position') as THREE.BufferAttribute | undefined;
                if (!posAttr) continue;
                let selAttr = geom.getAttribute('sel') as THREE.BufferAttribute | undefined;
                if (!selAttr || selAttr.count !== posAttr.count) {
                    selAttr = new THREE.BufferAttribute(new Float32Array(posAttr.count), 1);
                    geom.setAttribute('sel', selAttr);
                } else {
                    (selAttr.array as Float32Array).fill(0);
                }
                const arr = selAttr.array as Float32Array;
                for (const i of indices) {
                    if (i >= 0 && i < arr.length) arr[i] = 1.0;
                }
                selAttr.needsUpdate = true;
            }

            return;
        }

        // Default: element/mesh selection path
        const batchData: Array<[CustomBatchedMesh, string]> = [];
        // We currently select the entire model_key's CustomBatchedMesh (as in original code)
        const mesh = root as unknown as CustomBatchedMesh;
        for (const [, rangeId] of meshRangePairs) {
            batchData.push([mesh, rangeId]);
        }

        // Clear current selection and add batch of meshes
        selectedObjectStore.clearSelectedObjects();
        if (batchData.length > 0) {
            selectedObjectStore.addBatchofMeshes(batchData);
            console.log(`Selected ${batchData.length} mesh ranges for group members`);
        }

    } catch (error) {
        console.error('Error selecting group members:', error);
    }
}