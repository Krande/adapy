/** Named-set logic for the Results model panel — searching, selecting, and working out
 *  what "show the rest as wireframe" actually means.
 *
 *  Pure by design. Everything here is string and array work over the manifest's group
 *  list, so it unit-tests without a scene, a store, or a browser — and, just as
 *  importantly, without dragging a zustand store into the model worker's import graph.
 *  The one function that touches three.js lives in ``feaSetIsolation.ts`` next door.
 */

/** A named set as the manifest carries it. Matches ``FeaManifest["groups"][number]``,
 *  restated here so this module depends on no service. */
export interface FeaSet {
    name: string;
    members: string[];
    fe_object_type?: "node" | "element";
}

/** Draw ranges exist for elements only. A Sesam node set names vertices, which carry no
 *  triangles, so it can be listed and counted but never isolated — the panel says so
 *  rather than offering a control that quietly does nothing. */
export const isElementSet = (s: FeaSet): boolean => s.fe_object_type !== "node";

/** Case-insensitive substring match on the set name.
 *
 *  Substring, not prefix: Sesam set names are overwhelmingly compound
 *  (``Mini_area_dbl_btm``), so typing the part you remember — "btm" — has to find it.
 *  A prefix match would force people to remember names left-to-right, which is exactly
 *  what they cannot do across a few hundred sets. */
export function filterSets(sets: readonly FeaSet[], query: string): FeaSet[] {
    const q = query.trim().toLowerCase();
    if (!q) return [...sets];
    return sets.filter((s) => s.name.toLowerCase().includes(q));
}

/** Every member id across the named sets, de-duplicated, order preserved.
 *
 *  Multi-select is a union rather than an intersection because that is what picking two
 *  sets in a result viewer has always meant: show me both. Sets overlap freely in Sesam,
 *  so the de-duplication is load-bearing, not tidiness — a doubled id would be added to
 *  the selection store twice. */
export function unionMembers(sets: readonly FeaSet[], selected: ReadonlySet<string>): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const s of sets) {
        if (!selected.has(s.name)) continue;
        for (const m of s.members) {
            if (seen.has(m)) continue;
            seen.add(m);
            out.push(m);
        }
    }
    return out;
}

/** The ranges to ghost: everything the mesh draws that the selection does not name.
 *
 *  Computed against the MESH's range ids, not against the union of all sets. Sets rarely
 *  cover the whole model, so "everything not in another set" would leave unassigned
 *  elements solid and make the isolation look broken in precisely the models where sets
 *  matter most. */
export function complementRanges(allRangeIds: Iterable<string>, keep: Iterable<string>): string[] {
    const keepSet = keep instanceof Set ? keep : new Set(keep);
    const out: string[] = [];
    for (const id of allRangeIds) {
        if (!keepSet.has(id)) out.push(id);
    }
    return out;
}

/** Sum of members across the selected sets, counting shared members once.
 *
 *  Reported instead of adding the per-set counts up: two overlapping sets summing to more
 *  elements than the model contains is the kind of number that destroys trust in every
 *  other number on the panel. */
export function selectedMemberCount(sets: readonly FeaSet[], selected: ReadonlySet<string>): number {
    return unionMembers(sets, selected).length;
}

/** Shift-click range selection over the CURRENTLY VISIBLE rows.
 *
 *  Anchored on the visible list rather than the full one so a range selected under an
 *  active search filter takes what you can see, not the hidden rows in between. */
export function rangeBetween(visible: readonly FeaSet[], anchor: string, target: string): string[] {
    const a = visible.findIndex((s) => s.name === anchor);
    const b = visible.findIndex((s) => s.name === target);
    if (a < 0 || b < 0) return [target];
    const [lo, hi] = a <= b ? [a, b] : [b, a];
    return visible.slice(lo, hi + 1).map((s) => s.name);
}
