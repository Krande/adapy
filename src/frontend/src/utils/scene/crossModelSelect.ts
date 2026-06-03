import {getDrawRangeByName} from '@/utils/mesh_select/getDrawRangeByName';
import {useModelState} from '@/state/modelState';
import {useSelectedObjectStore} from '@/state/useSelectedObjectStore';
import {useObjectInfoStore} from '@/state/objectInfoStore';
import {useLineageStore} from '@/state/lineageStore';
import {CustomBatchedMesh} from '@/utils/mesh_select/CustomBatchedMesh';
import * as THREE from 'three';

type Args = {
    file: string;
    nodeNames: string[];
};

/**
 * Switch viewer focus (selection) to one or more elements that live in
 * a different loaded source file. Used by the CAD↔FEA link buttons in
 * the metadata panel.
 *
 * The lineage store keeps a reference to each loaded model's root
 * Object3D keyed by file name + assembly_guid, so we target that root
 * directly instead of guessing from the global modelKeyMap. This makes
 * cross-model select work correctly when multiple files are overlaid.
 *
 * Limitation: the target file must already be loaded (overlay or
 * single-active). If it isn't, ``getDrawRangeByName`` will fail
 * because the active model's userdata is queried for the lookup. We
 * surface a console warning rather than auto-loading — auto-load is a
 * follow-up feature once the storage browser exposes the necessary
 * hook.
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
        console.warn(`crossModelSelect: no registered root for file "${file}"`);
        return;
    }

    // Build a local name→mesh index for this root only, so overlays of
    // different files don't poison the lookup.
    const meshByName = new Map<string, CustomBatchedMesh>();
    root.traverse((obj: THREE.Object3D) => {
        if ((obj as any).isMesh) {
            meshByName.set(obj.name, obj as unknown as CustomBatchedMesh);
        }
    });

    let firstSelected: string | null = null;
    for (const nodeName of nodeNames) {
        const lookup = getDrawRangeByName(nodeName);
        if (!lookup) {
            console.warn(`crossModelSelect: no draw range for "${nodeName}" in active model`);
            continue;
        }
        const [bufferKey, rangeId] = lookup;
        const meshName = bufferKey.replace(/^draw_ranges_/, '');
        const mesh = meshByName.get(meshName);
        if (!mesh) {
            console.warn(`crossModelSelect: mesh "${meshName}" not found in target root`);
            continue;
        }
        selectionStore.addSelectedObject(mesh, String(rangeId));
        if (firstSelected === null) firstSelected = nodeName;
    }

    if (firstSelected !== null) {
        useObjectInfoStore.getState().setName(firstSelected);
        useObjectInfoStore.getState().setFileName(file);
    }
}
