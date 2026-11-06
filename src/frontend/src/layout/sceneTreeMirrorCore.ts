// Core's scene tree topic, `core:scene-tree` (`./sceneTreeMirror`): the main
// window's tree (the tree store's treeData), each row's visibility (read from
// the 3D batches) and the selected rows; a pop-out window puts the tree into its
// own tree store, so the stock scene tree renders it, and the rest into
// `sceneMirrorStore`. The rows' actions go back through `./sceneActions`.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {applyMirroredVisibility, useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {labelRoots} from "@/utils/tree_view/rootLabels";
import {showTreeSelection, treeSelectionIds} from "@/utils/tree_view/treeSelection";

import {sceneTreeTopic, type MirrorTreeNode, type SceneTreeSink, type SceneTreeSource, type SceneTreeTopicOptions} from "./sceneTreeMirror";
import {registerMirrorTopic} from "./windowMirror";

export const SCENE_TREE_TOPIC = "core:scene-tree";

/** Every row's visibility code that is not shown; loaded with the scene code, on the main window's first subscribe. */
let visibilityOf: ((container: TreeNodeData) => Map<string, number>) | null = null;

/** The main window's scene tree, from its stores. */
export function coreSceneTreeSource(): SceneTreeSource<MirrorTreeNode> {
    return {
        tree: () => useTreeViewStore.getState().treeData as MirrorTreeNode | null,
        visibility: (tree) => (visibilityOf ? visibilityOf(tree as unknown as TreeNodeData) : new Map()),
        selection: treeSelectionIds,
        subscribe(cb) {
            if (!visibilityOf) {
                void import("@/utils/tree_view/sceneTreeActions").then((m) => {
                    visibilityOf = m.rowVisibilityMap;
                    cb("visibility");
                });
            }
            const offTree = useTreeViewStore.subscribe((s, prev) => void (s.treeData !== prev.treeData && cb("tree")));
            const offVis = useVisibilityStore.subscribe(() => cb("visibility"));
            const offSel = useSelectedObjectStore.subscribe((s, prev) => void (s.selectedObjects !== prev.selectedObjects && cb("selection")));
            return () => {
                offTree();
                offVis();
                offSel();
            };
        },
    };
}

/** A pop-out window's copy: the tree into its tree store (its roots named as this window names them), the rest into `sceneMirrorStore`. */
export function coreSceneTreeSink(): SceneTreeSink {
    return {
        tree(tree) {
            const tv = useTreeViewStore.getState();
            if (!tree) tv.clearTreeData();
            else tv.setTreeData(labelRoots(tree as unknown as TreeNodeData, tv.rootLabelMode));
        },
        visibility: applyMirroredVisibility,
        selection(ids) {
            useSceneMirrorStore.setState({treeSelection: [...ids]});
            // A pop-out's tree is shown whenever it is mounted: its drawer flag is the main window's business.
            showTreeSelection(ids, true);
        },
    };
}

let registered = false;

/** Register `core:scene-tree` (once per page). */
export function registerCoreSceneTreeTopic(opts?: SceneTreeTopicOptions): void {
    if (registered) return;
    registered = true;
    registerMirrorTopic(sceneTreeTopic(SCENE_TREE_TOPIC, coreSceneTreeSource(), coreSceneTreeSink(), opts));
}

/** Forget the registration -- tests only. */
export function resetCoreSceneTreeTopic(): void {
    registered = false;
}
