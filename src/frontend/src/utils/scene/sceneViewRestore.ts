/**
 * The hidden / visible objects and the selection, kept with the scene record (sceneRecord.ts) so a
 * reload restores them with the models.
 *
 * CAPTURE reads the scene: per loaded model (its scene name), the element ids its batched meshes
 * hide -- or, under an isolation, the few they still draw -- and the selected ones. Ids only, never
 * scene objects: a reload builds new ones.
 *
 * APPLY runs after the restore has loaded the models: hides (or isolates) and selects by id in
 * whichever meshes of that model draw them. A streamed model can still be adding meshes when its
 * load reports done, so ids not found yet are tried again a few times before they are counted as
 * gone. Nothing of this is an undo step: the store is driven directly, and the history starts
 * clean afterwards.
 */

import type * as THREE from "three";

import {loadedSourceGroups} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

import {compactVisibility, planSourceView, type RecordedSourceView, type RecordedView} from "./sceneRecord";

/** A batched mesh, by what it can do rather than `instanceof` (a hot reload can leave meshes of
 *  an earlier copy of the class in the scene). */
function isBatched(o: unknown): o is CustomBatchedMesh {
    const m = o as CustomBatchedMesh;
    return !!o && typeof m.getHiddenRanges === "function" && m.drawRanges instanceof Map;
}

function batchedUnder(root: THREE.Object3D): CustomBatchedMesh[] {
    const out: CustomBatchedMesh[] = [];
    root.traverse((o) => {
        if (isBatched(o)) out.push(o);
    });
    return out;
}

/** The view of what is loaded now, or null when nothing is hidden or selected. */
export function captureView(): RecordedView | null {
    const sources: Record<string, RecordedSourceView> = {};
    const nameOf = new Map<THREE.Object3D, string>();
    for (const [name, group] of loadedSourceGroups.entries()) {
        nameOf.set(group, name);
        const hidden = new Set<string>();
        const drawn = new Set<string>();
        for (const mesh of batchedUnder(group)) {
            const h = mesh.getHiddenRanges();
            for (const id of mesh.drawRanges.keys()) (h.has(id) ? hidden : drawn).add(id);
        }
        // An element drawn by any of its meshes is not hidden.
        for (const id of drawn) hidden.delete(id);
        const vis = compactVisibility([...hidden], [...drawn]);
        if (vis) sources[name] = vis;
    }

    const selected = new Map<string, Set<string>>();
    for (const [obj, ids] of useSelectedObjectStore.getState().selectedObjects) {
        let name: string | undefined;
        for (let cur: THREE.Object3D | null = obj; cur && name === undefined; cur = cur.parent) name = nameOf.get(cur);
        if (name === undefined || ids.size === 0) continue;
        const set = selected.get(name) ?? new Set<string>();
        for (const id of ids) set.add(id);
        selected.set(name, set);
    }
    for (const [name, ids] of selected) sources[name] = {...(sources[name] ?? {}), selected: [...ids].sort()};

    return Object.keys(sources).length ? {v: 1, sources} : null;
}

const RETRY_MS = [500, 2000, 5000];
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Re-apply `view` to the models loaded now. Models of the view that are not loaded are skipped
 * quietly. Resolves with how many recorded ids were not found in the models that are.
 */
export async function applyRecordedView(
    view: RecordedView | null | undefined,
    retryMs: readonly number[] = RETRY_MS,
): Promise<{hidden: number; selected: number}> {
    const missing = {hidden: 0, selected: 0};
    if (!view) return missing;
    const done = new Set<CustomBatchedMesh>();
    let first = true;
    for (let attempt = 0; ; attempt++) {
        const batch: [THREE.Object3D, string][] = [];
        missing.hidden = 0;
        missing.selected = 0;
        for (const [name, sv] of Object.entries(view.sources)) {
            const group = loadedSourceGroups.get(name);
            if (!group) continue;
            const meshes = batchedUnder(group);
            const fresh = meshes.filter((m) => !done.has(m));
            const plan = planSourceView(sv, fresh.map((m) => new Set(m.drawRanges.keys())));
            fresh.forEach((mesh, i) => {
                const p = plan.perMesh[i];
                if (p.keep) mesh.isolateDrawRanges(p.keep);
                else if (p.hide.length) mesh.hideBatchDrawRange(p.hide);
                for (const id of p.select) batch.push([mesh, id]);
                done.add(mesh);
            });
            const all = planSourceView(sv, meshes.map((m) => new Set(m.drawRanges.keys())));
            missing.hidden += all.missing.hidden;
            missing.selected += all.missing.selected;
        }
        const sel = useSelectedObjectStore.getState();
        if (first && Object.values(view.sources).some((s) => s.selected?.length)) sel.clearSelectedObjects();
        if (batch.length) sel.addBatchofMeshes(batch);
        first = false;
        requestRender();
        // Restoring is not something to undo; nor may an undo reach back past it.
        useViewerHistoryStore.getState().reset();
        if ((missing.hidden === 0 && missing.selected === 0) || attempt >= retryMs.length) break;
        await sleep(retryMs[attempt]);
    }
    return missing;
}
