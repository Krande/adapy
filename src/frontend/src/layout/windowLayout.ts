// A pop-out window's arrangement: a `LayoutDoc` of the classic preset under the
// context `window/<id>`, whose split grid holds the window's panel groups. The
// grid keeps the viewport leaf every grid has (so the reducers and the stored
// shape are the classic ones), and the window draws it as nothing
// (`shownGrid(..., {viewport: false})`): the window is its groups alone.
//
// Pure and dependency-free.

import type { LayoutDoc } from "./layout";
import type { PanelId } from "./panel";
import { closePanel, dockInGridDoc, gridOf, updateGrid } from "./reducers";
import { ROOT_NODE, VIEWPORT_NODE, findNode, gridGroups, gridPanels, groupOf, setActiveTab, setGroupCollapsed, type DockEdge } from "./splitTree";
import type { WindowId } from "./windowBus";

/** The document context of pop-out window `id`. */
export const windowContext = (id: WindowId): string => `window/${id}`;

/** The panels a window's document holds, in document order. */
export function windowPanels(doc: LayoutDoc): PanelId[] {
  return gridPanels(gridOf(doc));
}

/**
 * Dock `id` in the window: shown where it is when it is there already; else
 * stacked into `group` (the group last used) when the window has it, else into
 * its first group, else as the window's first group.
 */
export function dockInWindow(doc: LayoutDoc, id: PanelId, group?: string | null): LayoutDoc {
  const grid = gridOf(doc);
  const own = groupOf(grid, id);
  if (own) return updateGrid(doc, (g) => setGroupCollapsed(setActiveTab(g, id), own.id, false));
  const into = (group && findNode(grid, group)?.kind === "group" ? group : null) ?? gridGroups(grid)[0]?.id ?? null;
  return dockInGridDoc(doc, id, into ? { node: into, edge: "center" } : { node: VIEWPORT_NODE, edge: "right" });
}

/** Dock `id` at an edge of the whole window, in a group of its own. */
export function dockAtWindowEdge(doc: LayoutDoc, id: PanelId, edge: DockEdge): LayoutDoc {
  return dockInGridDoc(doc, id, { node: ROOT_NODE, edge });
}

/** Take `id` out of the window. */
export function removeFromWindow(doc: LayoutDoc, id: PanelId): LayoutDoc {
  return closePanel(doc, id);
}
