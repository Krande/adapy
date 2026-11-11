// Selecting a group a loaded model carries (the Scene panel's Groups list). One path for the panel
// in the main window (with its viewer's stores) and for a pop-out's request (`core:select-group`,
// which runs it on the page's viewer).
//
// The selection itself is `./selectGroup`'s, through the viewport on screen. What is here is what
// only the three.js scene can answer: the model a group's owner is drawn in, and a group of nodes,
// which are points with a mask of their own.

import type * as THREE from "three";

import type {GroupInfo} from "@/state/sceneInfoStore";
import {modelStore} from "@/state/model_worker/modelStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {selectGroupMembers} from "@/utils/selectGroupMembers";
import {activeViewport} from "@/viewport/api/viewportApi";

import {selectGroup} from "./selectGroup";

export {sameGroup} from "./selectGroup";

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

/** The model key of the mesh that draws `group`'s owner in the three.js scene, or null. */
function meshModelKey(group: GroupInfo, deps: GroupSelectionDeps): string | null {
    // Resolve parent_name -> mesh inside the owning model's scene group when we know the source;
    // two loaded files with same-named objects would otherwise race on whichever the whole-scene
    // traversal finds first.
    const searchRoot = (group.source && deps.sourceGroup(group.source)) || deps.scene();
    let meshObj: CustomBatchedMesh | null = null;
    searchRoot?.traverse((obj) => {
        if (!meshObj && obj instanceof CustomBatchedMesh && obj.ada_ext_data?.name == group.parent_name) meshObj = obj;
    });
    return (meshObj as CustomBatchedMesh | null)?.unique_key ?? null;
}

/** Select `group`'s members in the scene, or clear the selection for null. */
export async function applyGroupSelection(group: GroupInfo | null, deps: GroupSelectionDeps): Promise<void> {
    const three = activeViewport()?.kind === "three";
    await selectGroup(group, {
        setSelectedGroup: deps.setSelectedGroup,
        setName: deps.setName,
        tree: () => useTreeViewStore.getState().treeData,
        modelKeyOf: (g) => (three ? meshModelKey(g, deps) : null),
        selectPoints: async (model, members) => {
            if (!three) return false;
            await selectGroupMembers(model, members, "node");
            return true;
        },
        objectsNamed: (model, members) => modelStore.getDrawRangesByMemberNames(model, members),
    });
}
