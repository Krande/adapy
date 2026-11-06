// Core's mirror topics and commands (`./windowMirror`): what core's mirrored
// panels read, and what they may ask the main window to do.
//
//   * `core:selection` -- the selected object as the object info panel shows it
//     (`objectInfoStore`: its name, tree node, picked face and point, attribute
//     JSON, source file). The three.js selection itself (meshes and draw ranges)
//     stays in the main window.
//   * `core:fea-session` -- whether an FEA result session is active (the panel
//     words a few things differently then).
//   * `core:select-node` -- select a tree node by id in the main window, as a
//     tree row click or the object info's navigation does; the new selection
//     comes back as `core:selection`.
//
// The scene code a command runs is loaded only when it runs, so a pop-out
// window that merely follows these does not load it.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useTreeViewStore} from "@/state/treeViewStore";

import {registerMirrorCommand, registerMirrorTopic} from "./windowMirror";

export const SELECTION_TOPIC = "core:selection";
export const FEA_SESSION_TOPIC = "core:fea-session";
export const SELECT_NODE_COMMAND = "core:select-node";

/** The object info store's data, without its panel flag and actions. */
export interface SelectionSnapshot {
  name: string | null;
  selectedNodeId: string | null;
  faceIndex: number | null;
  clickCoordinate: {x: number; y: number; z: number} | null;
  clickedFace: {faceId: number; seq: number} | null;
  jsonData: unknown;
  fileName: string | null;
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
    subscribe: (cb) =>
      useObjectInfoStore.subscribe((s, prev) => {
        // The panel flag is each window's own.
        if (s.show_info_box !== prev.show_info_box && s.name === prev.name && s.jsonData === prev.jsonData) return;
        cb();
      }),
    apply: (data) => useObjectInfoStore.setState({...data}),
  });
  registerMirrorTopic<{sessionActive: boolean}>({
    id: FEA_SESSION_TOPIC,
    snapshot: () => ({sessionActive: !!useFeaAnimationStore.getState().sessionActive}),
    subscribe: (cb) => useFeaAnimationStore.subscribe((s, prev) => void (s.sessionActive !== prev.sessionActive && cb())),
    apply: (data) => useFeaAnimationStore.setState({sessionActive: data.sessionActive}),
  });
  registerMirrorCommand<{id: string}>({
    id: SELECT_NODE_COMMAND,
    run: (args) => {
      const node = typeof args?.id === "string" ? findTreeNode(useTreeViewStore.getState().treeData, args.id) : null;
      if (!node) return;
      void import("@/utils/tree_view/treeNavigation").then((m) => m.selectTreeNode(node));
    },
  });
}
