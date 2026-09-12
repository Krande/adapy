// FEA streaming: the session handle, and the plugin-facing adapters onto it.
//
// Owns: the live view onto the open model session's FEA handle (`feaSession`),
// and the read/write adapters a shell or plugin uses to reach the active mesh
// and its per-element selection without importing the loader. Holds no state
// of its own -- every read and write goes straight through the session store,
// so there is no copy here to go stale.
//
// Inputs: `useModelSessionStore` (state/modelSession) for the handle;
// `useSelectedObjectStore` for the per-mesh selection it mirrors.

import * as THREE from "three";

import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";

/**
 * The streaming-FEA handle for the open model session, as a live view.
 *
 * It used to be a module-level `let active` in the loader, which outlived the
 * model it described: only `clearActiveFeaStreaming` could drop it, so every
 * teardown path had to remember to call it, and a missed one left the loader
 * pointing at a mesh that had already left the scene. The handle lives on the
 * session now (`state/modelSession`), and closing the session drops it along
 * with the group refs and the identity. Reading and writing `active` goes
 * straight through -- there is no copy here to go stale.
 */
export const feaSession = {
    get active(): FeaSessionHandle | null {
        return useModelSessionStore.getState().current()?.fea ?? null;
    },
    set active(handle: FeaSessionHandle | null) {
        useModelSessionStore.getState().ensure().fea = handle;
    },
};

/**
 * Does the loaded FEA model carry beam section geometry at all?
 *
 * `setBeamSolidsVisible` is a no-op without it — the bake only emits
 * ``beam_solids_url`` for a reader with section + axis info per beam, and only
 * when it was asked to. A UI that offers "beams as solid" needs to tell the two
 * cases apart: a toggle that flips and changes nothing reads as broken, where a
 * greyed one with a reason reads as a property of the model.
 */
export function hasBeamSolids(): boolean {
    return feaSession.active?.beamSolidMesh != null;
}

/** The active FEA mesh (a custom-batch THREE.Mesh carrying per-element
 *  ``drawRanges``), or null when no FEA model is loaded. Exposed so a plugin can
 *  drive element-level scene ops (isolate / highlight / attach overlays) off the
 *  same mesh core deforms — reached via the plugin SceneHandle, never imported. */
export function getActiveFeaMesh(): THREE.Mesh | null {
    return feaSession.active?.mesh ?? null;
}

/** Draw-range ids (e.g. ``E123``) currently selected on the active FEA mesh,
 *  or ``[]`` when nothing is selected / no FEA model is loaded. This is the same
 *  per-element selection the CustomBatchedMesh highlights (it reads the shared
 *  ``useSelectedObjectStore`` entry keyed on the active mesh). Exposed so a
 *  plugin drawing its own overlay on top of the FEA mesh can mirror core's
 *  selection highlight — reached via the plugin SceneHandle, never imported.
 *  Generic: names no plugin and returns the raw selection identity only. */
export function getActiveFeaSelectedRangeIds(): string[] {
    const mesh = feaSession.active?.mesh;
    if (!mesh) return [];
    const selected = useSelectedObjectStore.getState().selectedObjects.get(mesh);
    return selected ? Array.from(selected) : [];
}

/** Drive core's per-element selection on the active FEA mesh from a set of
 *  draw-range ids. This writes the SAME ``useSelectedObjectStore`` entry that a
 *  scene click writes, so the highlight uses the exact selection colour +
 *  CustomBatchedMesh path as click-select — a plugin listing results should call
 *  this instead of painting its own overlay. ``additive`` false (default)
 *  replaces the selection; true unions with the current one. No-op when no FEA
 *  model is loaded. Generic: names no plugin, takes raw range ids only. */
export function setActiveFeaSelectedRangeIds(rangeIds: string[], additive = false): void {
    const mesh = feaSession.active?.mesh;
    if (!mesh) return;
    const store = useSelectedObjectStore.getState();
    if (!additive) store.clearSelectedObjects();
    for (const id of rangeIds) store.addSelectedObject(mesh, id);
    requestRender();
}
