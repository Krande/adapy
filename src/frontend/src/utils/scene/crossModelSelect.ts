import {modelStore} from '@/state/model_worker/modelStore';
import {loadedSourceGroups, useModelState} from '@/state/modelState';
import {useSelectedObjectStore} from '@/state/useSelectedObjectStore';
import {useObjectInfoStore} from '@/state/objectInfoStore';
import {useLineageStore} from '@/state/lineageStore';
import {getViewerRuntime} from '@/state/viewerRuntime';
import {requestRender} from '@/state/perfStore';
import {CustomBatchedMesh} from '@/utils/mesh_select/CustomBatchedMesh';
import * as THREE from 'three';

type Args = {
    file: string;
    nodeNames: string[];
};

/**
 * Switch viewer focus (selection) to one or more elements that live in
 * a different loaded source file. Used by the CAD↔FEA link buttons in
 * the metadata panel, and by the Clashes / Joints panels to light up a
 * joint's members.
 *
 * The lineage store keeps a reference to each loaded model's root
 * Object3D keyed by file name + assembly_guid, so we target that root
 * directly instead of guessing from the global modelKeyMap. This makes
 * cross-model select work correctly when multiple files are overlaid.
 *
 * Limitation: the target file must already be loaded (overlay or
 * single-active); we surface a console warning rather than auto-loading
 * — auto-load is a follow-up feature once the storage browser exposes
 * the necessary hook.
 */
export async function selectInOtherModel({file, nodeNames}: Args): Promise<void> {
    const loadedSourceName = useModelState.getState().loadedSourceName;
    if (loadedSourceName !== file) {
        console.warn(
            `crossModelSelect: target file "${file}" is not the active model ("${loadedSourceName}"). ` +
                `Switch to it in the storage browser first.`,
        );
    }

    const selectionStore = useSelectedObjectStore.getState();
    selectionStore.clearSelectedObjects();

    // Pick the root that the lineage store registered for ``file``.
    // Falls back to scanning every loaded source group for the matching
    // file name if the lineage entry is missing (e.g. file pre-dates
    // the extension stamp).
    const assemblyGuid = useLineageStore.getState().getAssemblyGuidForFile(file);
    let root: THREE.Object3D | null = null;
    if (assemblyGuid) {
        const entry = useLineageStore.getState().entries.get(assemblyGuid);
        if (entry?.cad?.fileName === file) root = entry.cad.root;
        if (!root && entry) {
            const fea = entry.fea.find((f) => f.fileName === file);
            if (fea) root = fea.root;
        }
    }
    if (!root) {
        // The lineage store is only populated for a GLB that carries the ADA
        // extension's assembly guid + object_guids. A GLB written by a converter
        // that stamps neither — the native IFC→GLB path, for one — registers
        // nothing, and every caller here then selected nothing at all while
        // logging a warning no user sees. The loaded-source registry is keyed by
        // the same file name and needs no extension, so ask it before giving up:
        // lineage is what makes CAD↔FEA links resolvable, not what makes a model
        // selectable.
        root = loadedSourceGroups.get(file) ?? null;
    }
    if (!root) {
        console.warn(`crossModelSelect: no registered root for file "${file}"`);
        return;
    }

    // Build a local name→mesh index for this root only, so overlays of
    // different files don't poison the lookup — and recover the TARGET model's
    // cache key from its meshes (the old code queried the ACTIVE model's
    // userdata, which mis-resolved whenever the link target wasn't active).
    // Names repeat across primitive splits, so index every mesh per name and
    // let the draw range decide which one owns a hit.
    const meshesByName = new Map<string, CustomBatchedMesh[]>();
    const allMeshes: CustomBatchedMesh[] = [];
    let modelKey: string | null = null;
    root.traverse((obj: THREE.Object3D) => {
        if ((obj as any).isMesh) {
            const mesh = obj as unknown as CustomBatchedMesh;
            allMeshes.push(mesh);
            const named = meshesByName.get(obj.name) ?? [];
            named.push(mesh);
            meshesByName.set(obj.name, named);
            modelKey ??= (obj as any).unique_key ?? obj.userData?.['unique_hash'] ?? null;
        }
    });
    if (!modelKey) {
        // Same fallback shape as the root above: a mesh that carries no cache key
        // of its own is still reachable through the runtime's model-key map,
        // which is keyed by the very hash the worker stores its hierarchy under.
        getViewerRuntime().modelKeyMap.current?.forEach((group, key) => {
            if (modelKey === null && group === root) modelKey = key;
        });
    }
    if (!modelKey) {
        console.warn(`crossModelSelect: no model key recoverable from root of "${file}"`);
        return;
    }

    // One batched worker query resolves every member name to (meshName, rangeId)
    // against the TARGET model's hierarchy — all matches, not first-match-wins,
    // and the O(hierarchy) scan runs off the main thread.
    const pairs = await modelStore.getDrawRangesByMemberNames(modelKey, nodeNames);
    if (pairs.length === 0) {
        console.warn(`crossModelSelect: no draw ranges for ${nodeNames.length} member name(s) in "${file}"`);
        return;
    }
    // One batched store write, not one per range: `addSelectedObject` repaints the
    // mesh's selection groups on every call, so selecting a 40-member group used to
    // rebuild the same overlay 40 times.
    const batch: [CustomBatchedMesh, string][] = [];
    for (const [meshName, rangeId] of pairs) {
        const named = meshesByName.get(meshName);
        const target =
            named?.find((m) => m.drawRanges?.has(rangeId)) ??
            named?.[0] ??
            allMeshes.find((m) => m.drawRanges?.has(rangeId));
        if (!target) {
            console.warn(`crossModelSelect: mesh "${meshName}" not found in target root`);
            continue;
        }
        batch.push([target, String(rangeId)]);
    }
    if (batch.length === 0) return;
    selectionStore.addBatchofMeshes(batch);
    useObjectInfoStore.getState().setName(nodeNames[0]);
    useObjectInfoStore.getState().setFileName(file);
    // On-demand rendering: nothing else here moves the camera, so without this the
    // highlight only appears on the next orbit.
    requestRender();
}
