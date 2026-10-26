/** Show or hide the beams that only carry a mass to its COG.
 *
 *  GeniE models an equipment mass as a nodal mass at its centre of gravity, hung off
 *  the deck by a beam from each footprint node. adapy finds those beams and names
 *  them in a set with the ``dummy_mass`` role (see ``dummy_mass.py``); the manifest
 *  carries it, and ``useFemConceptsStore.dummyMassIds`` holds its members.
 *
 *  Two renderings, two rules:
 *
 *  * the section solids are what the toggle switches: each is the element as Sesam
 *    has it, a short stub off its footprint node, and someone checking the masses
 *    wants to see them;
 *  * the coloured result lines never show them. A line element's line runs node to
 *    node, which for these is footprint node to COG node: a web into one point that
 *    the model does not have. The bake leaves them out of the grey edge sidecars
 *    for the same reason.
 *
 *  Re-applied after anything that rewrites the hidden ranges wholesale (a group
 *  isolation, its clearing, a load), since those know nothing of this toggle.
 */

import * as THREE from "three";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

import {setResultLineSegmentsExcluded} from "./resultLineSegments";

function batchedMeshes(): CustomBatchedMesh[] {
    const scene = getViewerRuntime().scene.current;
    if (!scene) return [];
    const found: CustomBatchedMesh[] = [];
    scene.traverse((o: THREE.Object3D) => {
        if (o instanceof CustomBatchedMesh) found.push(o);
    });
    return found;
}

/** One Set per store array, so re-applying an unchanged list hands the line renderer the
 *  same Set and it can tell nothing changed. Holds the current model's ids only. */
let idSet: {source: string[]; set: ReadonlySet<string>} | null = null;
function idSetOf(ids: string[]): ReadonlySet<string> {
    if (idSet?.source !== ids) idSet = {source: ids, set: new Set(ids)};
    return idSet.set;
}

/** The group isolation in force (its members), or null. Showing the dummies again must
 *  not reveal the ones an isolation hid. */
let isolation: ReadonlySet<string> | null = null;

/** Apply the store's ``showDummyMasses`` to what is in the scene. ``isolated``: the
 *  members a group isolation now shows (null: none), from the isolation itself. */
export function applyDummyMassVisibility(isolated?: ReadonlySet<string> | null): void {
    if (isolated !== undefined) isolation = isolated;
    const {dummyMassIds, showDummyMasses} = useFemConceptsStore.getState();
    const ids = idSetOf(dummyMassIds);
    for (const m of batchedMeshes()) {
        setResultLineSegmentsExcluded(m, ids.size ? ids : null);
        if (ids.size === 0) continue;
        const own = dummyMassIds.filter((id) => m.drawRanges.has(id));
        if (own.length === 0) continue;
        if (!showDummyMasses) m.hideBatchDrawRange(own);
        else m.unhideBatchDrawRange(isolation ? own.filter((id) => isolation!.has(id)) : own);
    }
    requestRender();
}

/** Set the preference and apply it -- what the panel's checkbox wants. */
export function setDummyMassesVisible(visible: boolean): void {
    useFemConceptsStore.getState().setShowDummyMasses(visible);
    applyDummyMassVisibility();
}
