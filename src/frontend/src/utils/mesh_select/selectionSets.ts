/**
 * Select all and invert selection, as set math over the scene's draw ranges: pure (no three.js),
 * so it is tested on plain objects.
 *
 * What may be selected is what is SHOWN: a range in its mesh's hidden set, or a mesh that is not
 * visible itself or through an ancestor, is never taken (the same rule as every other selection
 * path, `./rangeVisibility`). Inverting is among the shown ranges only: a hidden range stays out
 * of the selection whether it was selected or not.
 */

import {objectShown} from "./rangeVisibility";

type Shown = Parameters<typeof objectShown>[0];
const shown = (o: RangeOwner): boolean => objectShown(o as unknown as Shown);

/** A batched mesh as these need it: its draw range ids and the ones it hides. */
export interface RangeOwner {
    visible?: boolean;
    /** Walked for `visible` (any object with one). */
    parent?: {visible?: boolean; parent?: unknown} | null;
    drawRanges: ReadonlyMap<string, unknown>;
    getHiddenRanges(): ReadonlySet<string>;
}

/** Every shown range of `meshes`, as a selection batch. */
export function allShownRanges<M extends RangeOwner>(meshes: Iterable<M>): [M, string][] {
    const out: [M, string][] = [];
    for (const mesh of meshes) {
        if (!shown(mesh)) continue;
        const hidden = mesh.getHiddenRanges();
        for (const id of mesh.drawRanges.keys()) if (!hidden.has(id)) out.push([mesh, id]);
    }
    return out;
}

/**
 * The shown ranges of `meshes` that are NOT in `selected` (ids per mesh): the selection an invert
 * leaves. A mesh with nothing selected is taken whole (its shown ranges).
 */
export function invertedRanges<M extends RangeOwner>(
    meshes: Iterable<M>,
    selected: ReadonlyMap<M, ReadonlySet<string>>,
): [M, string][] {
    const out: [M, string][] = [];
    for (const mesh of meshes) {
        if (!shown(mesh)) continue;
        const hidden = mesh.getHiddenRanges();
        const sel = selected.get(mesh);
        for (const id of mesh.drawRanges.keys()) {
            if (!hidden.has(id) && !sel?.has(id)) out.push([mesh, id]);
        }
    }
    return out;
}

/** Whether a scene (anything with `traverse`) holds a shown range to select: duck-typed, so a
 *  command's availability is asked without loading the mesh class. */
export function anyShownRange(scene: {traverse(cb: (o: unknown) => void): void} | null | undefined): boolean {
    let found = false;
    scene?.traverse((o) => {
        if (found) return;
        const m = o as Partial<RangeOwner>;
        if (typeof m.getHiddenRanges !== "function" || !m.drawRanges) return;
        if (shown(m as RangeOwner) && m.drawRanges.size > m.getHiddenRanges().size) found = true;
    });
    return found;
}

/** How a selection of `n` elements is named in the info panel. */
export function selectionLabel(kind: "all" | "inverted", n: number): string {
    return `${kind === "all" ? "All" : "Inverted selection"}: ${n} element${n === 1 ? "" : "s"}`;
}
