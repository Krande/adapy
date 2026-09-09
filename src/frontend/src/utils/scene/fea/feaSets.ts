/** Named-set arithmetic for isolating part of a streamed FEA model.
 *
 *  Pure by design: string and array work over the manifest's group list, so it
 *  unit-tests without a scene, a store or a browser. The one module that touches
 *  three.js is ``groupVisibility.ts`` next door.
 *
 *  Lived in the docked UI shell until a result plugin needed the same
 *  arithmetic to scope its own view to a set. The rule that a set member and a
 *  draw range are named differently belongs to core, which names both.
 */

/** A named set as the manifest carries it. Matches ``FeaManifest["groups"][number]``,
 *  restated here so this module depends on no service. */
export interface FeaSet {
    name: string;
    members: string[];
    fe_object_type?: "node" | "element";
}

/** Draw ranges exist for elements only. A Sesam node set names vertices, which carry no
 *  triangles, so it can be listed and counted but never isolated. */
export const isElementSet = (s: FeaSet): boolean => s.fe_object_type !== "node";

/** A set member as the mesh names its draw ranges, or null when it can never be one.
 *
 *  The manifest names element members ``EL123`` and node members ``P123``; the mesh keys
 *  its draw ranges ``E123``. Node members return null: a node carries no triangles, so it
 *  has no range to keep, and passing it through would only pad the keep-set with ids that
 *  cannot match.
 */
export function drawRangeIdFor(member: string): string | null {
    const el = /^EL(\d+)$/.exec(member);
    if (el) return `E${el[1]}`;
    if (/^E\d+$/.test(member)) return member;
    if (/^P\d+$/.test(member)) return null;
    // An id in a shape this does not know is passed through rather than dropped: a
    // future bake naming ranges some other way should fail visibly, not silently.
    return member;
}

/** Every member id across the named sets, as draw-range ids, de-duplicated, order preserved.
 *
 *  Multi-select is a union rather than an intersection because that is what picking two
 *  sets in a result viewer has always meant: show me both. Sets overlap freely in Sesam,
 *  so the de-duplication is load-bearing, not tidiness — a doubled id would be hidden and
 *  then unhidden by the same pass.
 */
export function unionMembers(sets: readonly FeaSet[], selected: ReadonlySet<string>): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const s of sets) {
        if (!selected.has(s.name)) continue;
        for (const raw of s.members) {
            const m = drawRangeIdFor(raw);
            if (m === null || seen.has(m)) continue;
            seen.add(m);
            out.push(m);
        }
    }
    return out;
}

/** The ranges to hide: everything the mesh draws that the selection does not name.
 *
 *  Computed against the MESH's range ids, not against the union of all sets. Sets rarely
 *  cover the whole model, so "everything not in another set" would leave unassigned
 *  elements visible and make the isolation look broken in precisely the models where sets
 *  matter most.
 */
export function complementRanges(allRangeIds: Iterable<string>, keep: Iterable<string>): string[] {
    const keepSet = keep instanceof Set ? keep : new Set(keep);
    const out: string[] = [];
    for (const id of allRangeIds) {
        if (!keepSet.has(id)) out.push(id);
    }
    return out;
}
