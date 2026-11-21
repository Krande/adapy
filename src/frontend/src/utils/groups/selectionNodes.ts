// The current selection as element-link nodes (`SelectionSnapshot.nodes`): each selected element of
// a model loaded from storage, folded up to whole levels exactly as "Group selection…" folds it, so a
// level picked in the tree is one node and the links under it are its own and its descendants'.

import type { ElementNodeRef } from "@/services/api/elementLinks";

import { captureSelection } from "./groupScene";
import type { GroupMember } from "./savedGroups";

/** One saved-group member as a node. */
export function nodeOfMember(m: GroupMember): ElementNodeRef {
  const elementId = m.source?.kind === "stored" ? m.source.element_id : null;
  return { target: m.target, element: m.element, path: m.path, ...(elementId ? { element_id: elementId } : {}) };
}

/** The selection's nodes; undefined when none is of a stored model (or the scene cannot say). */
export function selectionNodes(): ElementNodeRef[] | undefined {
  try {
    const { members } = captureSelection();
    return members.length ? members.map(nodeOfMember) : undefined;
  } catch {
    return undefined;
  }
}
