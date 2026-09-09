import * as THREE from "three";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";

// Which of a field's values are actually on screen.
//
// A categorical legend that names every material in the deck is describing the
// deck, not the picture. Section a model down to two frames, or isolate one
// group, and a twelve-entry legend beside a two-colour view is worse than no
// legend: the reader has to work out which entries are lies.
//
// The map is recorded where the colours are computed — one entry per drawn
// element, keyed by the same draw-range id the mesh hides by — so filtering it is
// a set lookup rather than a second pass over the field data.

/** Draw-range id → the field value that element was painted with. */
export type ValueByRange = Map<string, number>;

const KEY = "__feaValueByRange";

/** Record what each element was painted with. Called by the colouring kernels. */
export function recordValueByRange(mesh: THREE.Object3D, values: ValueByRange): void {
    mesh.userData[KEY] = values;
}

/** Forget it — the field changed, or the mesh is being replaced. */
export function clearValueByRange(mesh: THREE.Object3D): void {
    delete mesh.userData[KEY];
}

interface HideableMesh {
    getHiddenRanges?: () => ReadonlySet<string>;
}

/**
 * The distinct field values among elements that are currently drawn.
 *
 * Null when nothing has been recorded — the caller then shows everything, which
 * is the right answer for "we do not know" and matches the old behaviour.
 *
 * Hidden here means hidden by draw range: an isolated group, a manual hide.
 * Section-plane clipping is a shader effect on geometry that is still drawn, so
 * it is deliberately NOT counted — an element half-cut by a plane is still on
 * screen, and its material still belongs in the legend.
 */
export function visibleFieldValues(mesh: THREE.Object3D | null): Set<number> | null {
    if (!mesh) return null;
    const values = mesh.userData[KEY] as ValueByRange | undefined;
    if (!values || values.size === 0) return null;
    const hidden = (mesh as unknown as HideableMesh).getHiddenRanges?.() ?? new Set<string>();
    const out = new Set<number>();
    for (const [rangeId, value] of values) {
        if (hidden.has(rangeId)) continue;
        out.add(value);
    }
    return out;
}

/** The same, for whichever mesh the active result session is painting. */
export function visibleFieldValuesForSession(): Set<number> | null {
    return visibleFieldValues(useFeaAnimationStore.getState().mesh);
}
