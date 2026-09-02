import * as THREE from "three";

import {loadedSourceGroups, useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {nextModelOffsetX, type PlacedModel} from "@/utils/cellbuilder/modelPlacement";

// Per-source placement: nudge one loaded thing away from the others so several
// can be looked at side by side.
//
// Works on ANY loaded scene source — an uploaded GLB, a converted file, a
// procedural model's compiled result — because they are all groups in the same
// map keyed by source name. Nothing here knows what produced the geometry, which
// is the point: "put this one over there" should not have a different answer
// depending on where the geometry came from.
//
// Offsets are stored ON THE GROUP rather than in a store. The group is already
// the single source of truth for where a thing is drawn, and a parallel copy in
// a store would be one more thing to keep in step across loads, unloads and
// recompiles — which is precisely how the side-by-side offset went wrong.

/** Where a source sits relative to the shared model translation. */
export interface SourceOffset {
    x: number;
    y: number;
    z: number;
}

const ZERO: SourceOffset = {x: 0, y: 0, z: 0};

function baseTranslation(): SourceOffset {
    const t = useModelState.getState().translation;
    return {x: t?.x ?? 0, y: t?.y ?? 0, z: t?.z ?? 0};
}

/** The offset currently applied to a source, or zero when it is not loaded. */
export function getSourceOffset(sourceName: string): SourceOffset {
    const group = loadedSourceGroups.get(sourceName);
    if (!group) return {...ZERO};
    const base = baseTranslation();
    return {
        x: group.position.x - base.x,
        y: group.position.y - base.y,
        z: group.position.z - base.z,
    };
}

/** Place a source at an absolute offset from the shared translation.
 *
 * Absolute rather than relative so it is idempotent: re-applying after a reload
 * or a recompile lands in the same place instead of accumulating. */
export function setSourceOffset(sourceName: string, offset: Partial<SourceOffset>): void {
    const group = loadedSourceGroups.get(sourceName);
    if (!group) return;
    const base = baseTranslation();
    const cur = getSourceOffset(sourceName);
    group.position.set(
        base.x + (offset.x ?? cur.x),
        base.y + (offset.y ?? cur.y),
        base.z + (offset.z ?? cur.z),
    );
    requestRender();
}

/** X-width of a loaded source, or 0 when it is absent or unmeasurable. */
export function sourceWidthX(sourceName: string): number {
    const group = loadedSourceGroups.get(sourceName);
    if (!group) return 0;
    const box = new THREE.Box3().setFromObject(group);
    const w = box.max.x - box.min.x;
    return Number.isFinite(w) && w > 0 ? w : 0;
}

/** Every OTHER loaded source, as placement inputs. */
function othersPlaced(exclude: string): PlacedModel[] {
    const out: PlacedModel[] = [];
    for (const name of loadedSourceGroups.keys()) {
        if (name === exclude) continue;
        out.push({offsetX: getSourceOffset(name).x, width: sourceWidthX(name)});
    }
    return out;
}

/** Move a source to sit just past everything else already in the scene.
 *
 * Returns the offset applied, or null when the source is not loaded — the
 * caller can then say so rather than appearing to do nothing.
 *
 * Placement is +X only and past the far edge of every other loaded source, so
 * repeating it on several sources walks them along one axis in the order they
 * were placed. */
export function placeNextToExisting(sourceName: string): number | null {
    if (!loadedSourceGroups.has(sourceName)) return null;
    const offsetX = nextModelOffsetX(othersPlaced(sourceName), sourceWidthX(sourceName));
    setSourceOffset(sourceName, {x: offsetX});
    return offsetX;
}

/** Return a source to the shared origin. */
export function resetSourceOffset(sourceName: string): void {
    setSourceOffset(sourceName, ZERO);
}
