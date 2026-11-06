// Selecting a group a loaded model carries (the Scene panel's Groups list): the group's
// members are looked up in the model's scene group and selected. One path for the panel in
// the main window (with its viewer's stores) and for a pop-out's request (`core:select-group`,
// which runs it on the page's viewer).

import type * as THREE from "three";

import type {GroupInfo} from "@/state/sceneInfoStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {selectGroupMembers} from "@/utils/selectGroupMembers";

/** What selecting a group touches besides the 3D selection. */
export interface GroupSelectionDeps {
    setSelectedGroup(group: GroupInfo | null): void;
    /** The object info panel's name. */
    setName(name: string): void;
    /** The scene group a model was loaded into. */
    sourceGroup(source: string): THREE.Object3D | undefined;
    /** The whole scene, for a group with no known model. */
    scene(): THREE.Object3D | null | undefined;
}

/** Select `group`'s members in the scene, or clear the selection for null. */
export async function applyGroupSelection(group: GroupInfo | null, deps: GroupSelectionDeps): Promise<void> {
    if (!group) {
        deps.setSelectedGroup(null);
        deps.setName("");
        await selectGroupMembers("", [], undefined);
        return;
    }
    deps.setSelectedGroup(group);
    if (!group.members || group.members.length === 0) return;
    deps.setName(`Group: ${group.name}`);

    // Resolve parent_name -> mesh inside the owning model's scene group when we know the source;
    // two loaded files with same-named objects would otherwise race on whichever the whole-scene
    // traversal finds first.
    const searchRoot = (group.source && deps.sourceGroup(group.source)) || deps.scene();
    let meshObj: CustomBatchedMesh | null = null;
    searchRoot?.traverse((obj) => {
        if (!meshObj && obj instanceof CustomBatchedMesh && obj.ada_ext_data?.name == group.parent_name) meshObj = obj;
    });
    if (!meshObj) {
        console.warn(`Parent object ${group.parent_name} not found in scene`);
        return;
    }
    await selectGroupMembers((meshObj as CustomBatchedMesh).unique_key, group.members, group.fe_object_type);
}

/** Does `a` name the same group as `b` (a pop-out names one by model, name and owner)? */
export function sameGroup(a: {source?: string | null; name: string; parent_name: string}, b: {source?: string | null; name: string; parent_name: string}): boolean {
    return a.name === b.name && a.parent_name === b.parent_name && (a.source ?? null) === (b.source ?? null);
}
