import {create} from "zustand";
import type {NodeApi} from "react-arborist";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";

// The Scene's right-click menu, opened from a tree row or from the 3D view: one menu, one state,
// so the two can never offer different things for the same object.

export interface SceneMenuState {
    /** The row right-clicked (in the tree, or under the cursor in the 3D view). */
    row: TreeNodeData;
    /** The selection the menu acts on -- the row's, when it is part of one. */
    rows: readonly TreeNodeData[];
    x: number;
    y: number;
    /** The tree's own node for `row`, when the tree is mounted: what expanding works on. */
    node?: NodeApi<TreeNodeData>;
}

interface SceneMenuStore {
    menu: SceneMenuState | null;
    open: (menu: SceneMenuState) => void;
    close: () => void;
}

export const useSceneMenuStore = create<SceneMenuStore>()((set) => ({
    menu: null,
    open: (menu) => set({menu}),
    close: () => set({menu: null}),
}));
