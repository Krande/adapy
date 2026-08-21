/** Named-set logic for the Outliner's Groups section — what selecting a group makes
 *  visible.
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
 *  triangles, so it can be listed and counted but never isolated — the Outliner says so
 *  rather than offering a control that quietly does nothing. */
export const isElementSet = (s: FeaSet): boolean => s.fe_object_type !== "node";

/** Id of the Groups container row. */
export const GROUPS_ROOT_ID = "fea-groups";

const GROUP_PREFIX = "fea-group:";

/** Tree row id for one group. Prefixed so a group row is distinguishable from a real
 *  model node by its id alone — the selection handler branches on that, and a group whose
 *  name happens to match a mesh name must not make the two behave alike. */
export const groupNodeId = (name: string): string => `${GROUP_PREFIX}${name}`;

/** The group name behind a row id, or null when the row is not a group. */
export const groupNameFromId = (id: string): string | null =>
    id.startsWith(GROUP_PREFIX) ? id.slice(GROUP_PREFIX.length) : null;

/** One row of the Outliner tree. Structural on purpose: this module imports no component,
 *  and the shape is the subset of TreeNodeData a group row actually fills in. */
export interface GroupTreeNode {
    id: string;
    name: string;
    children: GroupTreeNode[];
    /** Right-aligned muted text — the member count, and the kind when it is not elements. */
    meta?: string;
}

/** The "Groups" row and its children, or null when the result carries no sets.
 *
 *  Null rather than an empty container: a permanent "Groups" row that expands to nothing
 *  would sit in the tree of every model that has no sets at all.
 */
export function buildGroupsRoot(sets: readonly FeaSet[]): GroupTreeNode | null {
    if (sets.length === 0) return null;
    const fmt = new Intl.NumberFormat();
    return {
        id: GROUPS_ROOT_ID,
        name: "Groups",
        meta: String(sets.length),
        children: sets.map((s) => ({
            id: groupNodeId(s.name),
            name: s.name,
            children: [],
            meta: isElementSet(s) ? fmt.format(s.members.length) : `${fmt.format(s.members.length)} n`,
        })),
    };
}

/** Every member id across the named sets, de-duplicated, order preserved.
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
        for (const m of s.members) {
            if (seen.has(m)) continue;
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

/** Sum of members across the selected sets, counting shared members once.
 *
 *  Reported instead of adding the per-set counts up: two overlapping sets summing to more
 *  elements than the model contains is the kind of number that destroys trust in every
 *  other number on screen.
 */
export function selectedMemberCount(sets: readonly FeaSet[], selected: ReadonlySet<string>): number {
    return unionMembers(sets, selected).length;
}
