// Saved groups against the LIVE scene: capture the current selection as members, and select a
// group's members again in whatever is loaded now. The matching itself is pure
// (`./savedGroups`); this file only reads the stores and paints the selection.

import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";
import { useObjectInfoStore } from "@/state/objectInfoStore";
import { useSelectedObjectStore } from "@/state/useSelectedObjectStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { requestRender } from "@/state/perfStore";
import type { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { buildTreeIndices, type TreeIndices } from "@/utils/tree_view/treeGraph";

import {
  collapseSelectedRows,
  geometryRowsUnder,
  groupScenePartsOf,
  membersForPickedRows,
  membersForRows,
  resolveMembers,
  rootMatchesTarget,
  type CapturedMembers,
  type GroupMember,
  type GroupMemberTarget,
  type GroupScenePart,
  type SavedGroup,
} from "./savedGroups";

let cachedRoot: TreeNodeData | null = null;
let cachedIndices: TreeIndices | null = null;

function indices(): TreeIndices | null {
  const root = useTreeViewStore.getState().treeData;
  if (!root) return null;
  if (root !== cachedRoot || !cachedIndices) {
    cachedIndices = buildTreeIndices(root);
    cachedRoot = root;
  }
  return cachedIndices;
}

/** The tree rows behind every selected draw range, by the unique `(model key, range id)`. */
function selectedRowIds(): Set<string> {
  const tv = useTreeViewStore.getState();
  const ids = new Set<string>();
  for (const [mesh, ranges] of useSelectedObjectStore.getState().selectedObjects) {
    const key: string | undefined = (mesh as any).unique_key ?? (mesh.userData ? mesh.userData["unique_hash"] : undefined);
    if (!key) continue;
    for (const rid of ranges) {
      const row = tv.findNodeByRangeId(key, rid);
      if (row) ids.add(row.id);
    }
  }
  return ids;
}

/** How many draw ranges are selected -- what "Group selection…" is enabled by. */
export function selectedRangeCount(selected: ReadonlyMap<unknown, ReadonlySet<string>>): number {
  let n = 0;
  for (const ranges of selected.values()) n += ranges.size;
  return n;
}

/** The current selection as group members, folded up to whole levels. */
export function captureSelection(): CapturedMembers & { readonly reason: string | null } {
  const idx = indices();
  if (!idx) return { members: [], skipped: [], reason: "The scene tree is not built yet." };
  const ids = selectedRowIds();
  if (ids.size === 0) {
    return { members: [], skipped: [], reason: "Nothing selected resolves to a row in the scene tree." };
  }
  const rows = collapseSelectedRows(ids, idx);
  return withReason(membersForRows(rows, idx, useAssetBrowserStore.getState().loaded));
}

function withReason(captured: CapturedMembers): CapturedMembers & { readonly reason: string | null } {
  return {
    ...captured,
    reason: captured.members.length ? null : "None of the selected objects belongs to a model loaded from storage.",
  };
}

/** Tree rows as picked (the Scene menu's rows: levels, leaves or model roots) as group members,
 *  folded up to whole levels exactly as `captureSelection` folds a selection. */
export function captureRows(rows: readonly TreeNodeData[]): CapturedMembers & { readonly reason: string | null } {
  const idx = indices();
  if (!idx) return { members: [], skipped: [], reason: "The scene tree is not built yet." };
  const captured = membersForPickedRows(rows, idx, useAssetBrowserStore.getState().loaded);
  if (captured.members.length === 0 && captured.skipped.length === 0) {
    return { ...captured, reason: "Nothing under the selection is drawn in the scene." };
  }
  return withReason(captured);
}

export interface GroupSelectionOutcome {
  /** Draw ranges selected. */
  readonly ranges: number;
  readonly notLoaded: readonly GroupMember[];
  readonly notFound: readonly GroupMember[];
}

/** The loaded models `members` are in now, each with the element ids under them (`groupScenePartsOf`). */
export function groupSceneParts(members: readonly GroupMember[]): {
  readonly parts: GroupScenePart[];
  readonly notLoaded: readonly GroupMember[];
  readonly notFound: readonly GroupMember[];
} {
  const idx = indices();
  if (!idx) return { parts: [], notLoaded: [...members], notFound: [] };
  return groupScenePartsOf(members, idx, useAssetBrowserStore.getState().loaded);
}

/** Select elements by WHICH MODEL (a member target) and element id (draw-range key), across models
 *  -- a group clash's members, whose names may repeat from one model to the next. Returns how many
 *  draw ranges were selected. */
export function selectModelElements(refs: readonly { readonly source: GroupMemberTarget; readonly element: string }[], label: string): number {
  const idx = indices();
  if (!idx) return 0;
  const loaded = useAssetBrowserStore.getState().loaded;
  const roots = idx.root.children ?? [];
  const byRoot = new Map<string, Map<string, TreeNodeData[]>>();
  const rowsOf = (root: TreeNodeData) => {
    let m = byRoot.get(root.id);
    if (!m) {
      m = new Map();
      for (const row of geometryRowsUnder([root])) {
        const list = m.get(String(row.rangeId));
        if (list) list.push(row);
        else m.set(String(row.rangeId), [row]);
      }
      byRoot.set(root.id, m);
    }
    return m;
  };
  const modelKeyMap = getViewerRuntime().modelKeyMap.current;
  const batch: [CustomBatchedMesh, string][] = [];
  for (const { source, element } of refs) {
    for (const root of roots) {
      if (!rootMatchesTarget(root, source, loaded)) continue;
      for (const row of rowsOf(root).get(element) ?? []) {
        if (row.rangeId == null || !row.node_name || !row.model_key) continue;
        const mesh = modelKeyMap?.get(row.model_key)?.getObjectByName(row.node_name) as CustomBatchedMesh | undefined;
        if (mesh) batch.push([mesh, row.rangeId]);
      }
    }
  }
  const sel = useSelectedObjectStore.getState();
  sel.clearSelectedObjects();
  if (batch.length) sel.addBatchofMeshes(batch);
  useObjectInfoStore.getState().setName(batch.length ? label : "");
  requestRender();
  return batch.length;
}

/** Select draw ranges by the LOADED SOURCE they are in and their element id (draw-range key) -- a
 *  convex-contact run's members, already mapped to what is loaded (`clashContactPlan`). Returns how
 *  many draw ranges were selected. */
export function selectSourceElements(refs: readonly { readonly sourceName: string; readonly element: string }[], label: string): number {
  const idx = indices();
  if (!idx) return 0;
  const wanted = new Map<string, Set<string>>();
  for (const { sourceName, element } of refs) {
    let set = wanted.get(sourceName);
    if (!set) wanted.set(sourceName, (set = new Set()));
    set.add(element);
  }
  const modelKeyMap = getViewerRuntime().modelKeyMap.current;
  const batch: [CustomBatchedMesh, string][] = [];
  for (const root of idx.root.children ?? []) {
    const elements = root.source_name ? wanted.get(root.source_name) : undefined;
    if (!elements) continue;
    for (const row of geometryRowsUnder([root])) {
      if (row.rangeId == null || !row.node_name || !row.model_key || !elements.has(String(row.rangeId))) continue;
      const mesh = modelKeyMap?.get(row.model_key)?.getObjectByName(row.node_name) as CustomBatchedMesh | undefined;
      if (mesh) batch.push([mesh, row.rangeId]);
    }
  }
  const sel = useSelectedObjectStore.getState();
  sel.clearSelectedObjects();
  if (batch.length) sel.addBatchofMeshes(batch);
  useObjectInfoStore.getState().setName(batch.length ? label : "");
  requestRender();
  return batch.length;
}

/** Select every member of `group` that is in the scene now, all models together. */
export function selectSavedGroup(group: SavedGroup): GroupSelectionOutcome {
  const idx = indices();
  if (!idx) return { ranges: 0, notLoaded: [...group.members], notFound: [] };
  const { rows, notLoaded, notFound } = resolveMembers(group.members, idx, useAssetBrowserStore.getState().loaded);

  const modelKeyMap = getViewerRuntime().modelKeyMap.current;
  const batch: [CustomBatchedMesh, string][] = [];
  for (const row of geometryRowsUnder(rows)) {
    if (row.rangeId == null || !row.node_name || !row.model_key) continue;
    const mesh = modelKeyMap?.get(row.model_key)?.getObjectByName(row.node_name) as CustomBatchedMesh | undefined;
    if (mesh) batch.push([mesh, row.rangeId]);
  }

  // One clear and ONE batch for every member across every model: clearing per member would leave
  // only the last model selected.
  const sel = useSelectedObjectStore.getState();
  sel.clearSelectedObjects();
  if (batch.length) sel.addBatchofMeshes(batch);
  const info = useObjectInfoStore.getState();
  info.setName(batch.length ? `Group: ${group.name}` : "");
  // No single tree row stands for a group; a stale level id would let the panel claim one.
  info.setSelectedNodeId(rows.length === 1 ? rows[0].id : null);
  requestRender();
  return { ranges: batch.length, notLoaded, notFound };
}
