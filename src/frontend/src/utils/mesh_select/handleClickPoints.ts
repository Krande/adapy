import * as THREE from "three";
import {useModelState} from "../../state/modelState";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useOptionsStore} from "../../state/optionsStore";
import {showSelectedPoint, showSelectedPoints} from "../scene/highlightSelectedPoint";

import {gpuPointPicker} from "./GpuPointPicker";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {queryNameFromRangeId, queryPointDrawRange, queryPointRangeByRangeId} from "./queryMeshDrawRange";
import {useTreeViewStore} from "../../state/treeViewStore";
import {findNodeById} from "../tree_view/findNodeById";
import {perform_selection} from "./perform_selection";

export async function handleClickPoints(
    intersect: THREE.Intersection,
    event: MouseEvent
): Promise<void> {
    if (event.button === 2) return;

    const shiftKey = event.shiftKey;

    const useGpu = useOptionsStore.getState().useGpuPointPicking;

    let obj = intersect.object as THREE.Points;
    let idx: number | null = (typeof intersect.index === 'number') ? intersect.index : null;
    let worldPosition = intersect.point.clone();

    if (useGpu) {
        const pick = gpuPointPicker.pickAt(event.clientX, event.clientY);
        if (pick) {
            obj = pick.object;
            idx = pick.index;
            worldPosition = pick.worldPosition.clone();
        }
    } else {
        // CPU fallback using raycaster data with morph deformation applied
        if (idx != null && obj.geometry && (obj.geometry as THREE.BufferGeometry).getAttribute) {
            const geom = obj.geometry as THREE.BufferGeometry;
            const posAttr = geom.getAttribute('position') as THREE.BufferAttribute | undefined;
            if (posAttr && idx >= 0 && idx < posAttr.count) {
                const local = new THREE.Vector3(
                    posAttr.getX(idx),
                    posAttr.getY(idx),
                    posAttr.getZ(idx)
                );
                // Apply morph targets if present
                const morphs = (geom.morphAttributes && geom.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
                const rel = geom.morphTargetsRelative === true;
                const influences: number[] | undefined = (obj as any).morphTargetInfluences;
                if (morphs && influences && morphs.length === influences.length) {
                    let sum = 0;
                    for (let i = 0; i < morphs.length; i++) {
                        const inf = influences[i] || 0;
                        if (inf === 0) continue;
                        sum += inf;
                        const mp = morphs[i];
                        const mx = mp.getX(idx), my = mp.getY(idx), mz = mp.getZ(idx);
                        if (rel) {
                            local.x += mx * inf;
                            local.y += my * inf;
                            local.z += mz * inf;
                        } else {
                            local.x = local.x * (1 - sum) + mx * inf;
                            local.y = local.y * (1 - sum) + my * inf;
                            local.z = local.z * (1 - sum) + mz * inf;
                        }
                    }
                }
                worldPosition = local.applyMatrix4(obj.matrixWorld);
            }
        }
    }

    // adjust for any translation (model offset)
    const translation = useModelState.getState().translation;
    const clickPosition = worldPosition.clone();
    if (translation) clickPosition.sub(translation);
    useObjectInfoStore.getState().setClickCoordinate(clickPosition);
    const hash = obj.userData['unique_hash'] as string;
    if (idx == null) {
        return
    }
    const drawRange = await queryPointDrawRange(hash, obj.name, idx);
    // update object info
    if (!drawRange) {
        console.warn("selected mesh has no draw range");
        return;
    }

    const [rangeId] = drawRange;

    // Update unified selection state (supports multi-select with Shift)
    await perform_selection(obj, shiftKey, rangeId);

    // Build multi-point highlight for all selected point ranges
    const ps = useOptionsStore.getState().pointSize;

    // Helper to compute deformed world position for a given points object and index
    const getDeformedWorldPos = (pointsObj: THREE.Points, index: number): THREE.Vector3 | null => {
        const geom = pointsObj.geometry as THREE.BufferGeometry;
        const posAttr = geom.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (!posAttr || index < 0 || index >= posAttr.count) return null;
        const local = new THREE.Vector3(
            posAttr.getX(index),
            posAttr.getY(index),
            posAttr.getZ(index)
        );
        const morphs = (geom.morphAttributes && geom.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
        const rel = geom.morphTargetsRelative === true;
        const influences: number[] | undefined = (pointsObj as any).morphTargetInfluences;
        if (morphs && influences && morphs.length === influences.length) {
            let sum = 0;
            for (let i = 0; i < morphs.length; i++) {
                const inf = influences[i] || 0;
                if (inf === 0) continue;
                sum += inf;
                const mp = morphs[i];
                const mx = mp.getX(index), my = mp.getY(index), mz = mp.getZ(index);
                if (rel) {
                    local.x += mx * inf;
                    local.y += my * inf;
                    local.z += mz * inf;
                } else {
                    local.x = local.x * (1 - sum) + mx * inf;
                    local.y = local.y * (1 - sum) + my * inf;
                    local.z = local.z * (1 - sum) + mz * inf;
                }
            }
        }
        return local.applyMatrix4(pointsObj.matrixWorld);
    };

    const positions: THREE.Vector3[] = [];
    // Iterate through selected objects and gather all selected point positions
    for (const [o, selectedRanges] of useSelectedObjectStore.getState().selectedObjects) {
        const pointsObj = o as THREE.Points;
        if (!(pointsObj as any).isPoints) continue;
        const key: string | undefined = pointsObj.userData ? pointsObj.userData['unique_hash'] : undefined;
        if (!key) continue;
        const meshName = pointsObj.name;
        for (const rid of selectedRanges) {
            const range = await queryPointRangeByRangeId(key, meshName, rid);
            if (!range) continue;
            const [start, length] = range;
            const end = start + length;
            for (let i = start; i < end; i++) {
                const wp = getDeformedWorldPos(pointsObj, i);
                if (wp) positions.push(wp);
            }
        }
    }

    if (positions.length > 0) {
        showSelectedPoints(positions, ps);
    } else {
        // Fallback to last clicked point only
        showSelectedPoint(worldPosition.clone(), ps);
    }

    const selected = await queryNameFromRangeId(hash, rangeId);
    if (!selected) {
        console.warn("selected mesh has no name");
        return;
    }
    useObjectInfoStore.getState().setName(selected);

    // update tree selection using unified store
    const treeViewStore = useTreeViewStore.getState();
    if (treeViewStore.treeData && treeViewStore.tree && !treeViewStore.isTreeCollapsed) {
        // flag programmatic change
        // @ts-ignore
        treeViewStore.tree.isProgrammaticChange = true;

        const node_ids: string[] = [];
        for (const [o, selectedRanges] of useSelectedObjectStore.getState().selectedObjects) {
            const lookupKey: string | undefined = (o as any).unique_key ?? (o.userData ? o.userData['unique_hash'] : undefined);
            if (!lookupKey) continue;
            for (const rid of selectedRanges) {
                const nodeName = await queryNameFromRangeId(lookupKey, rid);
                if (!nodeName) continue;
                const node = findNodeById(treeViewStore.treeData, nodeName);
                if (node) node_ids.push(node.id);
            }
        }

        const lastNode = findNodeById(treeViewStore.treeData, selected);
        treeViewStore.tree.setSelection({
            ids: node_ids,
            mostRecent: lastNode,
            anchor: lastNode,
        });
        if (lastNode) treeViewStore.tree.scrollTo({id: lastNode.id});

        // @ts-ignore
        treeViewStore.tree.isProgrammaticChange = false;
    }
}
