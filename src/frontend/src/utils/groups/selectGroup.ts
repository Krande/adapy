// Selecting a group of a loaded model (the Scene panel's "FEM groups" picker), on whichever
// viewport is on screen: the group's members become object refs and the viewport selects them.
//
// A group read from a GLB's `ADA_EXT_data` extension names its members by object name: the model's
// hierarchy says which objects those are. A set of a streamed FEA result names element ids, which
// are the result's objects as they stand. Either way the model is found by the source it was
// loaded from, in the Scene tree's roots. No three.js in this module.

import type {GroupInfo} from "@/state/sceneInfoStore";
import {drawRangeIdFor} from "@/utils/scene/fea/feaSets";
import {activeViewport, type ObjectRef} from "@/viewport/api/viewportApi";

/** A row of the Scene tree, as far as finding a model goes: a root carries its model's key and the
 *  source it was loaded from; several roots hang under one row that carries neither. */
export interface ModelRow {
    model_key?: string | null;
    source_name?: string | null;
    children?: readonly ModelRow[];
}

/** What selecting a group touches besides the 3D selection, and what it asks of its caller. */
export interface SelectGroupDeps {
    setSelectedGroup(group: GroupInfo | null): void;
    /** The object info panel's name. */
    setName(name: string): void;
    /** The Scene tree's top row. */
    tree(): ModelRow | null;
    /** The model key of the group's model when the caller can find it in its own scene; null to
     *  have it looked up by the source it was loaded from. */
    modelKeyOf?(group: GroupInfo): string | null;
    /** Select a group of nodes drawn as points, where the viewport needs more than a selection for
     *  it (the three.js viewport masks them). Returns false to have them selected as objects. */
    selectPoints?(model: string, members: string[]): Promise<boolean>;
    /** The objects named `members` in model `model`, as [GLB node, object id] pairs. */
    objectsNamed(model: string, members: string[]): Promise<[string, string][]>;
}

/** The key of the model loaded from `source` (any one model when it is not given), from the Scene
 *  tree's roots; null when no such model is listed. */
export function modelKeyOfSource(tree: ModelRow | null, source: string | undefined): string | null {
    if (!tree) return null;
    const roots = tree.model_key != null ? [tree] : tree.children ?? [];
    for (const root of roots) {
        if (root.model_key != null && (source === undefined || root.source_name === source)) return root.model_key;
    }
    return null;
}

/** The members of a streamed result's set as objects of `model`: one ref, given new values for
 *  each. Members that are not elements (nodes) are left out. */
export function* elementRefs(model: string, members: readonly string[]): Generator<ObjectRef> {
    const ref: ObjectRef = {model, id: ""};
    for (const member of members) {
        const id = drawRangeIdFor(member);
        if (id === null) continue;
        ref.id = id;
        yield ref;
    }
}

/** [GLB node, object id] pairs as objects of `model`: one ref, given new values for each. */
export function* pairRefs(model: string, pairs: readonly (readonly [string, string])[]): Generator<ObjectRef> {
    const ref: ObjectRef = {model, id: "", node: null};
    for (const pair of pairs) {
        ref.node = pair[0];
        ref.id = pair[1];
        yield ref;
    }
}

/** Select `group`'s members on the viewport on screen, or clear the selection for null. Returns
 *  how many objects were selected. */
export async function selectGroup(group: GroupInfo | null, deps: SelectGroupDeps): Promise<number> {
    if (!group) {
        deps.setSelectedGroup(null);
        deps.setName("");
        activeViewport()?.clearSelection();
        return 0;
    }
    deps.setSelectedGroup(group);
    if (!group.members || group.members.length === 0) return 0;
    const named = () => deps.setName(`Group: ${group.name}`);
    const viewport = activeViewport();

    // A streamed result's sets belong to the one result on screen, named by its source.
    const model = deps.modelKeyOf?.(group) ?? modelKeyOfSource(deps.tree(), group.streamed ? group.parent_name : group.source);
    if (model === null || !viewport) {
        named();
        if (viewport) console.warn(`The model of group ${group.name} (${group.parent_name}) is not in the scene`);
        return 0;
    }
    // The name is part of the selection's step, so an undo takes both back.
    if (group.streamed) return viewport.setSelection(elementRefs(model, group.members), {within: named});
    if (group.fe_object_type === "node" && (await deps.selectPoints?.(model, group.members))) {
        named();
        return group.members.length;
    }
    const pairs = await deps.objectsNamed(model, group.members);
    if (pairs.length === 0) console.warn("No objects found for the members of group", group.name);
    return viewport.setSelection(pairRefs(model, pairs), {within: named});
}

/** Does `a` name the same group as `b` (a pop-out names one by model, name and owner)? */
export function sameGroup(a: {source?: string | null; name: string; parent_name: string}, b: {source?: string | null; name: string; parent_name: string}): boolean {
    return a.name === b.name && a.parent_name === b.parent_name && (a.source ?? null) === (b.source ?? null);
}
