// Core's mirror topics and commands (`./windowMirror`): what core's mirrored
// panels read, and what they may ask the main window to do.
//
//   * `core:selection` -- the selected object as the object info panel shows it
//     (`objectInfoStore`: its name, tree node, picked face and point, attribute
//     JSON, source file), plus what the panel derives from the three.js
//     selection: how many ranges are selected and the parent level its up arrow
//     selects (`sceneMirrorStore`). The three.js selection itself (meshes and
//     draw ranges) stays in the main window.
//   * `core:fea-session` -- whether an FEA result session is active (the panel
//     words a few things differently then).
//   * `core:fea-tree` -- the FEA result's set and property rows, the isolation
//     and what the rows say of the picked element (`@/utils/scene/fea/feaTree`);
//     its actions are scene actions (`core:fea-isolate`, ...).
//   * the scene actions' commands (`./sceneActions`): `core:run-command` (core's
//     hide / hide others / show all, clear selection, fits, undo / redo),
//     `core:select-node`, `core:select-parent`, and the scene tree's row
//     commands. A changed selection comes back as `core:selection`.
//
// The scene code a command runs is loaded only when it runs, so a pop-out
// window that merely follows these does not load it.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {parentLevelName, rowsById, selectedRangeCount} from "@/utils/tree_view/treeSelection";

import {installFeaTree} from "@/utils/scene/fea/feaTree";

import {registerSceneActionCommands, SCENE_ACTION_COMMANDS} from "./sceneActions";
import {registerCoreSceneTreeTopic, resetCoreSceneTreeTopic} from "./sceneTreeMirrorCore";
import {registerMirrorTopic} from "./windowMirror";

export const SELECTION_TOPIC = "core:selection";
export const FEA_SESSION_TOPIC = "core:fea-session";
export const SELECT_NODE_COMMAND = SCENE_ACTION_COMMANDS.selectNode;

/** The object info store's data, without its panel flag and actions, and what the panel derives from the 3D selection. */
export interface SelectionSnapshot {
  name: string | null;
  selectedNodeId: string | null;
  faceIndex: number | null;
  clickCoordinate: {x: number; y: number; z: number} | null;
  clickedFace: {faceId: number; seq: number} | null;
  jsonData: unknown;
  fileName: string | null;
  /** How many draw ranges are selected. */
  rangeCount: number;
  /** The parent level "up" would select; null when there is none. */
  parentName: string | null;
}

export function selectionSnapshot(): SelectionSnapshot {
  const s = useObjectInfoStore.getState();
  return {
    name: s.name,
    selectedNodeId: s.selectedNodeId,
    faceIndex: s.faceIndex,
    clickCoordinate: s.clickCoordinate,
    clickedFace: s.clickedFace,
    // Plain JSON from the server; anything else would not cross a window.
    jsonData: s.jsonData == null ? null : JSON.parse(JSON.stringify(s.jsonData)),
    fileName: s.fileName,
    rangeCount: selectedRangeCount(),
    parentName: parentLevelName(),
  };
}

/** The tree node of `id` in the loaded tree, or null. */
export function findTreeNode(root: TreeNodeData | null, id: string): TreeNodeData | null {
  if (!root) return null;
  const stack: TreeNodeData[] = [root];
  while (stack.length) {
    const n = stack.pop()!;
    if (n.id === id) return n;
    if (Array.isArray(n.children)) for (const c of n.children) stack.push(c);
  }
  return null;
}

let registered = false;

/** Register core's topics and commands (once per page). */
export function registerCoreMirror(): void {
  if (registered) return;
  registered = true;
  registerMirrorTopic<SelectionSnapshot>({
    id: SELECTION_TOPIC,
    snapshot: selectionSnapshot,
    subscribe: (cb) => {
      const offInfo = useObjectInfoStore.subscribe((s, prev) => {
        // The panel flag is each window's own.
        if (s.show_info_box !== prev.show_info_box && s.name === prev.name && s.jsonData === prev.jsonData) return;
        cb();
      });
      // The count and the parent level follow the 3D selection and the tree.
      const offSel = useSelectedObjectStore.subscribe((s, prev) => void (s.selectedObjects !== prev.selectedObjects && cb()));
      const offTree = useTreeViewStore.subscribe((s, prev) => void (s.treeData !== prev.treeData && cb()));
      return () => {
        offInfo();
        offSel();
        offTree();
      };
    },
    apply: ({rangeCount, parentName, ...info}) => {
      useObjectInfoStore.setState({...info});
      useSceneMirrorStore.setState({rangeCount: rangeCount ?? 0, parentName: parentName ?? null});
    },
  });
  registerMirrorTopic<{sessionActive: boolean}>({
    id: FEA_SESSION_TOPIC,
    snapshot: () => ({sessionActive: !!useFeaAnimationStore.getState().sessionActive}),
    subscribe: (cb) => useFeaAnimationStore.subscribe((s, prev) => void (s.sessionActive !== prev.sessionActive && cb())),
    apply: (data) => useFeaAnimationStore.setState({sessionActive: data.sessionActive}),
  });
  registerCoreSceneTreeTopic();
  registerSceneActionCommands(rowsById);
  // The FEA result's sets and properties as tree rows, and `core:fea-tree` for a pop-out's.
  installFeaTree();
}

/** Forget the registration -- tests only (with `resetMirror`). */
export function resetCoreMirror(): void {
  registered = false;
  resetCoreSceneTreeTopic();
}
