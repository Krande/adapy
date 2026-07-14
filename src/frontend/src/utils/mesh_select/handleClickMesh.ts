// utils/mesh_select/handleClickMesh.ts
import * as THREE from "three";
import {CustomBatchedMesh} from "./CustomBatchedMesh";
import {useModelState} from "@/state/modelState";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {queryMeshDrawRange, queryNameFromRangeId, queryFaceInfo} from "./queryMeshDrawRange";
import {useOptionsStore} from "@/state/optionsStore";
import {setFaceHighlight, clearFaceHighlight} from "./faceHighlight";
import {perform_selection} from "./perform_selection";
import {query_ws_server_mesh_info} from "./handlers/send_mesh_selected_info_callback";
import {useTreeViewStore} from "@/state/treeViewStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {simulationDataRef} from "@/state/refs";
import {SimulationDataExtensionMetadata} from "@/extensions/design_and_analysis_extension";

export async function handleClickMesh(
    intersect: THREE.Intersection,
    event: MouseEvent,
    prefilledRangeId?: string,
): Promise<void> {
    if (event.button === 2) return;

    const mesh = intersect.object as CustomBatchedMesh;
    const faceIndex = intersect.faceIndex ?? 0;
    const shiftKey = event.shiftKey;

    // adjust for any translation
    const translation = useModelState.getState().translation;
    const clickPosition = intersect.point.clone();
    if (translation) clickPosition.sub(translation);
    useObjectInfoStore.getState().setClickCoordinate(clickPosition);

    if (!mesh.is_design && mesh.ada_ext_data != null) {
        simulationDataRef.current = (mesh.ada_ext_data as SimulationDataExtensionMetadata);
    }

    // GPU-pick path supplies the rangeId directly so we skip the
    // faceIndex → rangeId worker round-trip entirely; the raycast
    // fallback still uses it.
    let rangeId: string;
    // The mesh-name key the metadata cache actually uses — mesh.name usually, but the native GLB path
    // keys by node<node_id>, so the draw-range lookup falls back to it. Track which one resolved so
    // face-level picking below queries the SAME key (its earlier bug was using mesh.name unconditionally).
    let resolvedMeshName = mesh.name;
    if (prefilledRangeId !== undefined) {
        rangeId = prefilledRangeId;
    } else {
        // ← await the worker lookup
        let drawRange = await queryMeshDrawRange(mesh.unique_key, resolvedMeshName, faceIndex);
        if (!drawRange) {
            if (mesh.userData?.node_id != null) {
                resolvedMeshName = `node${mesh.userData?.node_id}`;
                drawRange = await queryMeshDrawRange(mesh.unique_key, resolvedMeshName, faceIndex);
            }
            if (!drawRange) {
                console.warn("selected mesh has no draw range");
                return;
            }
        }
        [rangeId] = drawRange;
    }

    const facePicking = useOptionsStore.getState().faceLevelPicking;

    // Face-level picking (opt-in): resolve the clicked triangle to its source face region (STEP/IFC
    // #id) and highlight just that face in the selection colour. Try both mesh-name keys (mesh.name /
    // node<node_id>) since the GPU-pick path supplies a prefilled rangeId and skips the draw-range
    // lookup that would otherwise resolve the key. faceIndex comes from the refined single-mesh raycast.
    if (facePicking && typeof intersect.faceIndex === "number") {
        let fi = await queryFaceInfo(mesh.unique_key, mesh.name, intersect.faceIndex);
        if (!fi && mesh.userData?.node_id != null) {
            fi = await queryFaceInfo(mesh.unique_key, `node${mesh.userData?.node_id}`, intersect.faceIndex);
        }
        useObjectInfoStore.getState().setClickedFace(fi ? {faceId: fi.faceId, seq: fi.seq} : null);
        if (fi) setFaceHighlight(mesh, fi.start, fi.length);
        else clearFaceHighlight();
    } else {
        useObjectInfoStore.getState().setClickedFace(null);
        clearFaceHighlight();
    }

    // Faces mode highlights ONLY the clicked face (the rest of the solid keeps its normal colour), so
    // skip the whole-object selection overlay and drop any lingering one. The Properties panel still
    // populates — it's gated on the object name set below, not on the selection store.
    if (!facePicking) {
        await perform_selection(mesh, shiftKey, rangeId);
    } else {
        useSelectedObjectStore.getState().clearSelectedObjects();
    }

    // update object info
    const last_selected_name = await queryNameFromRangeId(mesh.unique_key, rangeId);
    if (!last_selected_name) {
        console.warn("selected mesh has no name");
        return;
    }
    useObjectInfoStore.getState().setName(last_selected_name);

    // Fire-and-forget the metadata request so the Properties panel
    // populates without blocking the rest of the click handler. The
    // active file name scopes the backend's lookup so overlay loads
    // don't collide on name; ``loadedSourceName`` carries the most
    // recently activated source.
    const activeFile = useModelState.getState().loadedSourceName;
    useObjectInfoStore.getState().setFileName(activeFile);
    useObjectInfoStore.getState().setJsonData(null);
    void query_ws_server_mesh_info(last_selected_name, faceIndex, activeFile);

    // update tree selection — only in Solid mode; Faces mode doesn't select the whole object
    const treeViewStore = useTreeViewStore.getState();
    if (!facePicking && treeViewStore.treeData && treeViewStore.tree && !treeViewStore.isTreeCollapsed) {
        // flag programmatic change
        // @ts-ignore
        treeViewStore.tree.isProgrammaticChange = true;

        const node_ids: string[] = [];
        for (const [m, selectedRanges] of useSelectedObjectStore.getState().selectedObjects) {
            // Determine lookup key per object type
            const lookupKey: string | undefined = (m as any).unique_key ?? (m.userData ? m.userData['unique_hash'] : undefined);
            if (!lookupKey) continue;
            for (const rid of selectedRanges) {
                // Resolve by (model_key, rangeId) — the unique numeric node id —
                // never by display name, which repeats thousands of times in
                // real CAD models and made the tree highlight the wrong row.
                const node = treeViewStore.findNodeByRangeId(lookupKey, rid);
                if (node) node_ids.push(node.id);
            }
        }

        const lastNode = treeViewStore.findNodeByRangeId(mesh.unique_key, rangeId);
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
