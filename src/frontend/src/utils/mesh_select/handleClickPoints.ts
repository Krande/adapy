import * as THREE from "three";
import {useModelState} from "../../state/modelState";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useOptionsStore} from "../../state/optionsStore";
import {enablePointSelectionMask} from "../scene/pointsImpostor";
import {selectedMaterial} from "../default_materials";

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

    // Capture previously selected Points objects (to clear masks if they become unselected)
    const prevSelectedPoints = new Set<THREE.Points>();
    for (const [o] of useSelectedObjectStore.getState().selectedObjects) {
        if ((o as any).isPoints) prevSelectedPoints.add(o as THREE.Points);
    }

    // Update unified selection state (supports multi-select with Shift)
    await perform_selection(obj, shiftKey, rangeId);

    // Shader-based per-vertex selection coloring for Points
    // 1) Build per-object selected indices from current selection map
    const perObjIndices = new Map<THREE.Points, number[]>();
    for (const [o, selectedRanges] of useSelectedObjectStore.getState().selectedObjects) {
        const pointsObj = o as THREE.Points;
        if (!(pointsObj as any).isPoints) continue;
        const key: string | undefined = pointsObj.userData ? pointsObj.userData['unique_hash'] : undefined;
        if (!key) continue;
        const meshName = pointsObj.name;
        const list: number[] = [];
        for (const rid of selectedRanges) {
            const range = await queryPointRangeByRangeId(key, meshName, rid);
            if (!range) continue;
            const [start, length] = range;
            const end = start + length;
            for (let i = start; i < end; i++) list.push(i);
        }
        perObjIndices.set(pointsObj, list);
    }
    // If nothing is selected (rare edge), highlight just the clicked index for feedback
    if (perObjIndices.size === 0 && idx != null && (obj as any).isPoints) {
        perObjIndices.set(obj, [idx]);
    }

    // Helper to clear mask
    const clearMask = (pointsObj: THREE.Points) => {
        const geom = pointsObj.geometry as THREE.BufferGeometry;
        const posAttr = geom.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (!posAttr) return;
        let selAttr = geom.getAttribute('sel') as THREE.BufferAttribute | undefined;
        if (!selAttr || selAttr.count !== posAttr.count) {
            selAttr = new THREE.BufferAttribute(new Float32Array(posAttr.count), 1);
            geom.setAttribute('sel', selAttr);
        } else {
            (selAttr.array as Float32Array).fill(0);
            selAttr.needsUpdate = true;
        }
    };

    // 3) Apply selection masks and selection color via shader
    for (const [pointsObj, indices] of perObjIndices) {
        enablePointSelectionMask(pointsObj, selectedMaterial.color);
        const geom = pointsObj.geometry as THREE.BufferGeometry;
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
        prevSelectedPoints.delete(pointsObj);
    }
    // 4) Clear masks on previously selected Points that are no longer selected
    for (const p of prevSelectedPoints) clearMask(p);

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
