/**
 * The Model panel's TREE: the procedural model's components arranged the way the
 * panel's "View" option asks -- flat, or as the structure tree.
 *
 * Pure: builder cells + groups in, nodes out. The panel (core's `ComponentList`
 * and a shell's own copy) renders the nodes; nothing here knows the store.
 *
 *   flat   every component, in document order (the list as it always was)
 *   tree   Structure -> space -> the equipment and openings placed in it;
 *          components in no space under "Unassigned"
 *
 * Components in no structure gather under "No structure". The structure layer is
 * left out altogether while the model defines no structure (then the tree starts
 * at the spaces).
 *
 * A group row carries the ids of everything under it (select / frame / cascade)
 * and an include MARK for its checkbox: a structure's is the structure's own
 * INCLUDE (the Structures sheet), a space's or a gathering row's is the cascade
 * over the own flags of what it holds -- on, off, or mixed.
 *
 * A plugin's refs (state/cellbuilder/refsSlice.ts), when given, hang off the group
 * rows as `refs`, not among `children`: a ref goes under its space when it names a
 * space cell, else under its structure, else (a ref for every structure, "*", or for
 * a structure the model does not have) under a trailing "All structures" row. In the
 * flat view, which has no group rows, a ref that names a space hangs off that space's
 * own row (`ItemNode.refs`), and the rest gather under a trailing "Other" row. A panel
 * that draws no refs draws the tree as it was.
 */

import type {BuilderCell} from "@/state/cellbuilder/types";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import {isGroupIncluded, type CellGroup} from "./groups";
import {isItemIncluded, itemCell, itemIncludeState, itemStructure, type IncludeState} from "./include";

export type ModelView = "flat" | "tree";

export const MODEL_VIEWS: readonly {id: ModelView; label: string; hint: string}[] = [
  {id: "flat", label: "Flat", hint: "Every component in one list"},
  {id: "tree", label: "Structure tree", hint: "Each structure, its spaces, and what is placed in each space"},
];

export const DEFAULT_MODEL_VIEW: ModelView = "flat";

export function isModelView(v: unknown): v is ModelView {
  return v === "flat" || v === "tree";
}

/** A remembered view, including the two grouped views this panel once had
 * ("structure", "cell"): both read as the structure tree. */
export function modelViewFromStored(raw: unknown): ModelView {
  if (isModelView(raw)) return raw;
  return raw === "structure" || raw === "cell" ? "tree" : DEFAULT_MODEL_VIEW;
}

/** What a group row's checkbox shows. */
export type IncludeMark = "on" | "off" | "mixed";

export interface ItemNode {
  type: "item";
  key: string;
  cell: BuilderCell;
  /** The component's effective state (own flag and its structure's). */
  state: IncludeState;
  depth: number;
  /** The plugins' refs placed under this row (a space's, in the flat view); absent when none. */
  refs?: RefNode[];
}

export interface GroupNode {
  type: "group";
  key: string;
  /** structure: a defined structure; none: "No structure"; cell: a space;
   * unassigned: a structure's components placed in no space; all: the plugins' refs
   * for every structure or for one the model lacks; other: the refs in the flat view. */
  kind: "structure" | "none" | "cell" | "unassigned" | "all" | "other";
  label: string;
  group: CellGroup | null;
  cell: BuilderCell | null;
  /** The structure this row is in or is (undefined under "No structure"). */
  structure: string | undefined;
  /** Every component under the row, at any depth. */
  itemIds: string[];
  include: IncludeMark;
  /** For dimming: "excluded-by-structure" for everything inside a left-out structure. */
  state: IncludeState;
  depth: number;
  children: ModelNode[];
  /** The plugins' refs placed under this row, after its children; absent when none. */
  refs?: RefNode[];
}

/** A plugin's ref in the tree, under a group row's `refs`. */
export interface RefNode {
  type: "ref";
  key: string;
  ref: BuilderRef;
  depth: number;
}

export type ModelNode = ItemNode | GroupNode;

export interface ModelTreeInput {
  cells: Record<string, BuilderCell>;
  groups: readonly CellGroup[];
  loftMembers?: readonly {NAME: string; STRUCTURE_NAME?: string}[];
  /** The plugins' refs, placed as `refs` on the group rows. */
  refs?: readonly BuilderRef[];
}

export const NO_STRUCTURE_LABEL = "No structure";
export const UNASSIGNED_LABEL = "Unassigned";
export const ALL_STRUCTURES_LABEL = "All structures";
export const OTHER_LABEL = "Other";

const KIND_RANK: Record<BuilderCell["kind"], number> = {cell: 0, loft: 1, equipment: 2, opening: 3};

/** The cascade mark over components' OWN include flags. */
export function cascadeMark(items: readonly Pick<BuilderCell, "kind" | "params">[]): IncludeMark {
  let on = 0;
  for (const it of items) if (isItemIncluded(it)) on++;
  if (on === items.length) return "on";
  if (on === 0) return "off";
  return "mixed";
}

/** The ids of every component under a node (an item is its own). */
export function nodeItemIds(node: ModelNode): string[] {
  return node.type === "item" ? [node.cell.id] : node.itemIds;
}

function buildCellTree(view: ModelView, input: ModelTreeInput): ModelNode[] {
  const {cells, groups} = input;
  const loftMembers = input.loftMembers ?? [];
  const all = Object.values(cells);
  const item = (c: BuilderCell, depth: number): ItemNode => ({
    type: "item",
    key: `item:${c.id}`,
    cell: c,
    state: itemIncludeState(c, cells, groups, loftMembers),
    depth,
  });
  if (view === "flat") return all.map((c) => item(c, 0));

  const byStructure = new Map<string | undefined, BuilderCell[]>();
  for (const c of all) {
    const s = itemStructure(c, cells, groups, loftMembers);
    const list = byStructure.get(s);
    if (list) list.push(c);
    else byStructure.set(s, [c]);
  }
  const ordered = (list: BuilderCell[]) =>
    list.map((c, i) => ({c, i})).sort((a, b) => KIND_RANK[a.c.kind] - KIND_RANK[b.c.kind] || a.i - b.i).map((x) => x.c);

  const structureChildren = (members: BuilderCell[], structure: string | undefined, depth: number, dimmed: boolean): ModelNode[] => {
    const state: IncludeState = dimmed ? "excluded-by-structure" : "included";
    const spaces = members.filter((c) => c.kind === "cell");
    const rest = members.filter((c) => c.kind !== "cell");
    const placed = new Set<string>();
    const nodes: ModelNode[] = spaces.map((space) => {
      const inside = rest.filter((c) => itemCell(c, cells)?.id === space.id);
      for (const c of inside) placed.add(c.id);
      return {
        type: "group",
        key: `cell:${space.id}`,
        kind: "cell",
        label: space.name,
        group: null,
        cell: space,
        structure,
        itemIds: [space.id, ...inside.map((c) => c.id)],
        include: cascadeMark([space, ...inside]),
        state: dimmed ? "excluded-by-structure" : itemIncludeState(space, cells, groups, loftMembers),
        depth,
        children: ordered(inside).map((c) => item(c, depth + 1)),
      } satisfies GroupNode;
    });
    const loose = ordered(rest.filter((c) => !placed.has(c.id)));
    if (loose.length) {
      nodes.push({
        type: "group",
        key: `unassigned:${structure ?? ""}`,
        kind: "unassigned",
        label: UNASSIGNED_LABEL,
        group: null,
        cell: null,
        structure,
        itemIds: loose.map((c) => c.id),
        include: cascadeMark(loose),
        state,
        depth,
        children: loose.map((c) => item(c, depth + 1)),
      });
    }
    return nodes;
  };

  // No structure defined at all: the structure layer says nothing, leave it out.
  if (groups.length === 0) return structureChildren(all, undefined, 0, false);

  const out: ModelNode[] = [];
  for (const g of groups) {
    const members = byStructure.get(g.name) ?? [];
    const included = isGroupIncluded(g);
    out.push({
      type: "group",
      key: `structure:${g.name}`,
      kind: "structure",
      label: g.name,
      group: g,
      cell: null,
      structure: g.name,
      itemIds: members.map((c) => c.id),
      include: included ? "on" : "off",
      state: included ? "included" : "excluded-by-structure",
      depth: 0,
      children: structureChildren(members, g.name, 1, !included),
    });
  }
  const loose = byStructure.get(undefined) ?? [];
  if (loose.length) {
    out.push({
      type: "group",
      key: "structure:",
      kind: "none",
      label: NO_STRUCTURE_LABEL,
      group: null,
      cell: null,
      structure: undefined,
      itemIds: loose.map((c) => c.id),
      include: cascadeMark(loose),
      state: "included",
      depth: 0,
      children: structureChildren(loose, undefined, 1, false),
    });
  }
  return out;
}

/** Hang the plugins' refs off the rows of `nodes` (see the header). */
function placeRefs(view: ModelView, nodes: ModelNode[], refs: readonly BuilderRef[]): ModelNode[] {
  if (!refs.length) return nodes;
  const add = (row: GroupNode | ItemNode, ref: BuilderRef) => {
    (row.refs ??= []).push({type: "ref", key: `ref:${ref.id}`, ref, depth: row.depth + 1});
  };
  const trailing = (kind: "all" | "other", label: string, list: readonly BuilderRef[]): GroupNode => {
    const on = list.filter((r) => r.include).length;
    const row: GroupNode = {
      type: "group",
      key: `${kind}:`,
      kind,
      label,
      group: null,
      cell: null,
      structure: undefined,
      itemIds: [],
      include: on === list.length ? "on" : on === 0 ? "off" : "mixed",
      state: "included",
      depth: 0,
      children: [],
    };
    for (const r of list) add(row, r);
    return row;
  };
  if (view === "flat") {
    // No group rows: a space's own row takes the refs that name it.
    const spaceRows = new Map<string, ItemNode>();
    for (const n of nodes) if (n.type === "item" && n.cell.kind === "cell" && !spaceRows.has(n.cell.name)) spaceRows.set(n.cell.name, n);
    const other: BuilderRef[] = [];
    for (const ref of refs) {
      const row = ref.space ? spaceRows.get(ref.space) : undefined;
      if (row) add(row, ref);
      else other.push(ref);
    }
    return other.length ? [...nodes, trailing("other", OTHER_LABEL, other)] : nodes;
  }

  const spaces = new Map<string, GroupNode>();
  const structures = new Map<string, GroupNode>();
  const walk = (list: readonly ModelNode[]) => {
    for (const n of list) {
      if (n.type !== "group") continue;
      if (n.kind === "cell" && n.cell && !spaces.has(n.cell.name)) spaces.set(n.cell.name, n);
      if (n.kind === "structure" && n.group) structures.set(n.group.name, n);
      walk(n.children);
    }
  };
  walk(nodes);
  const everywhere: BuilderRef[] = [];
  for (const ref of refs) {
    const row = (ref.space ? spaces.get(ref.space) : undefined)
      ?? (ref.structure && ref.structure !== "*" ? structures.get(ref.structure) : undefined);
    if (row) add(row, ref);
    else everywhere.push(ref);
  }
  return everywhere.length ? [...nodes, trailing("all", ALL_STRUCTURES_LABEL, everywhere)] : nodes;
}

export function buildModelTree(view: ModelView, input: ModelTreeInput): ModelNode[] {
  return placeRefs(view, buildCellTree(view, input), input.refs ?? []);
}
