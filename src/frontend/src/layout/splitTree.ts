// The split grid of the classic layout, as data: a tree of splits whose leaves
// are panel groups (tabs) and the one viewport. A split lays its children out in
// a row or a column, each taking its fraction (`sizes`) of the split; a group
// shows one of its panels at a time; the viewport leaf is where the canvas is
// drawn, and is never closed, moved into a group or removed.
//
// Every function here returns a new tree and leaves its input untouched; a call
// that changes nothing returns its input. Dependency-free, so `node --test`
// covers it directly.

import type { PanelId } from "./panel";

export type SplitDir = "row" | "col";
export type DockEdge = "left" | "right" | "top" | "bottom";

/**
 * A node held at a size in px along its parent split, instead of a fraction: a
 * dock that keeps its width when the window resizes. `min` / `max` bound what a
 * divider drag may make of it.
 */
export interface FixedSize {
  px: number;
  min?: number;
  max?: number;
}

export interface SplitNode {
  kind: "split";
  id: string;
  dir: SplitDir;
  children: GridNode[];
  /** Each child's fraction of the split; they sum to 1. A `fixed` child's entry is kept but not used. */
  sizes: number[];
  fixed?: FixedSize;
}

/** How a group shows its panels: one at a time behind tabs, all stacked, or stacked while there is room. */
export type GroupArrangement = "tabs" | "stack" | "auto";

export interface GroupNode {
  kind: "group";
  id: string;
  panels: PanelId[];
  active: PanelId | null;
  fixed?: FixedSize;
  /** Shown as a narrow strip of its panels' icons; one of them restores it. */
  collapsed?: boolean;
  /** Default `"tabs"`. */
  arrangement?: GroupArrangement;
}

/** The width (or height) of a collapsed group's strip, in px. */
export const COLLAPSED_PX = 28;

export interface ViewportNode {
  kind: "viewport";
}

export type GridNode = SplitNode | GroupNode | ViewportNode;

/** The viewport leaf's id as a target. */
export const VIEWPORT_NODE = "viewport";
/** The whole grid as a target: an edge of it splits outside everything. */
export const ROOT_NODE = "root";

/** The grid a user starts from: the viewport alone. */
export const DEFAULT_GRID: GridNode = Object.freeze({ kind: "viewport" }) as ViewportNode;

/** Where a panel docks: beside a node (an edge splits it) or into a group (center, at `index`). */
export interface GridTarget {
  /** A group's id, `VIEWPORT_NODE` or `ROOT_NODE`. */
  node: string;
  edge: DockEdge | "center";
  /** For `center`: the position in the group's panels the panel goes before; absent: at the end. */
  index?: number;
}

export const nodeId = (n: GridNode): string => (n.kind === "viewport" ? VIEWPORT_NODE : n.id);

/** The share of the target's room a panel docked at its edge takes. */
export function splitShare(node: string): number {
  if (node === VIEWPORT_NODE) return 0.3;
  if (node === ROOT_NODE) return 0.25;
  return 0.5;
}

export const edgeDir = (edge: DockEdge): SplitDir => (edge === "left" || edge === "right" ? "row" : "col");
const before = (edge: DockEdge) => edge === "left" || edge === "top";

// ---------------------------------------------------------------------------
// Queries.
// ---------------------------------------------------------------------------

/** The groups, in document order. */
export function gridGroups(root: GridNode): GroupNode[] {
  if (root.kind === "group") return [root];
  if (root.kind === "split") return root.children.flatMap(gridGroups);
  return [];
}

/** The group `panel` is in, or null. */
export function groupOf(root: GridNode, panel: PanelId): GroupNode | null {
  return gridGroups(root).find((g) => g.panels.includes(panel)) ?? null;
}

/** Every panel the grid holds, in document order. */
export function gridPanels(root: GridNode): PanelId[] {
  return gridGroups(root).flatMap((g) => g.panels);
}

export function findNode(root: GridNode, id: string): GridNode | null {
  if (nodeId(root) === id) return root;
  if (root.kind !== "split") return null;
  for (const c of root.children) {
    const hit = findNode(c, id);
    if (hit) return hit;
  }
  return null;
}

function ids(root: GridNode, out: Set<string> = new Set()): Set<string> {
  if (root.kind !== "viewport") out.add(root.id);
  if (root.kind === "split") for (const c of root.children) ids(c, out);
  return out;
}

/** A fresh id: `prefix` and the next number not used in `root`. */
export function nextId(root: GridNode, prefix: "g" | "s"): string {
  const used = ids(root);
  let n = 1;
  while (used.has(`${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}

// ---------------------------------------------------------------------------
// Tidying: no empty group, no split of one child, no split inside a split of
// the same direction, sizes that sum to 1.
// ---------------------------------------------------------------------------

/** Fractions that sum to 1; a size that is not a positive number takes the mean of the others. */
function normalised(sizes: readonly number[]): number[] {
  const valid = sizes.filter((s) => Number.isFinite(s) && s > 0);
  const mean = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : 1;
  const ok = sizes.map((s) => (Number.isFinite(s) && s > 0 ? s : mean));
  const sum = ok.reduce((a, b) => a + b, 0);
  return ok.map((s) => s / sum);
}

function tidy(node: GridNode | null): GridNode | null {
  if (!node) return null;
  if (node.kind === "viewport") return node;
  if (node.kind === "group") return node.panels.length === 0 ? null : node;
  const kids: GridNode[] = [];
  const sizes: number[] = [];
  node.children.forEach((c, i) => {
    const t = tidy(c);
    if (!t) return;
    const s = node.sizes[i] ?? 0;
    // A fixed split stays a node of its own: flattened, its size would be lost.
    if (t.kind === "split" && t.dir === node.dir && !t.fixed) {
      t.children.forEach((cc, j) => {
        kids.push(cc);
        sizes.push(s * t.sizes[j]);
      });
    } else {
      kids.push(t);
      sizes.push(s);
    }
  });
  if (kids.length === 0) return null;
  if (kids.length === 1) return kids[0];
  const changed = kids.length !== node.children.length || kids.some((k, i) => k !== node.children[i]);
  const ns = normalised(sizes);
  if (!changed && ns.every((s, i) => Math.abs(s - node.sizes[i]) < 1e-9)) return node;
  return { ...node, children: kids, sizes: ns };
}

/** Replace the node `id` by what `fn` makes of it (null: remove it). */
function mapNode(root: GridNode, id: string, fn: (n: GridNode) => GridNode | null): GridNode | null {
  if (nodeId(root) === id) return fn(root);
  if (root.kind !== "split") return root;
  let changed = false;
  const children = root.children.map((c) => {
    const m = mapNode(c, id, fn);
    if (m !== c) changed = true;
    return m;
  });
  if (!changed) return root;
  const kept = children.map((c, i) => ({ c, s: root.sizes[i] })).filter((x): x is { c: GridNode; s: number } => x.c !== null);
  return { ...root, children: kept.map((x) => x.c), sizes: kept.map((x) => x.s) };
}

const settle = (n: GridNode | null): GridNode => tidy(n) ?? DEFAULT_GRID;

// ---------------------------------------------------------------------------
// Changes.
// ---------------------------------------------------------------------------

/** Take `panel` out of the grid; its group goes when it empties, and the splits around it collapse. */
export function removeFromGrid(root: GridNode, panel: PanelId): GridNode {
  const g = groupOf(root, panel);
  if (!g) return root;
  const at = g.panels.indexOf(panel);
  const panels = g.panels.filter((p) => p !== panel);
  const active = g.active === panel ? (panels[Math.min(at, panels.length - 1)] ?? null) : g.active;
  return settle(mapNode(root, g.id, () => (panels.length ? { ...g, panels, active } : null)));
}

/** Put `node` beside the node `target` (or the whole grid, `ROOT_NODE`) at `edge`, taking `share` of its room. */
function insertBeside(root: GridNode, target: string, node: GridNode, edge: DockEdge, share: number): GridNode {
  const dir = edgeDir(edge);
  const first = before(edge);
  const wrap = (t: GridNode): GridNode => {
    if (t.kind === "split" && t.dir === dir && target === ROOT_NODE) {
      const sizes = t.sizes.map((s) => s * (1 - share));
      return {
        ...t,
        children: first ? [node, ...t.children] : [...t.children, node],
        sizes: first ? [share, ...sizes] : [...sizes, share],
      };
    }
    // The wrapping split takes the target's place in its parent, and so its fixed size.
    const fixed = t.kind !== "viewport" && target !== ROOT_NODE ? t.fixed : undefined;
    let inner: GridNode = t;
    if (fixed && t.kind !== "viewport") {
      const { fixed: _moved, ...rest } = t;
      inner = rest as GridNode;
    }
    return {
      kind: "split",
      id: nextId(root, "s"),
      dir,
      children: first ? [node, inner] : [inner, node],
      sizes: first ? [share, 1 - share] : [1 - share, share],
      ...(fixed ? { fixed } : {}),
    };
  };
  if (target === ROOT_NODE || nodeId(root) === target) return settle(wrap(root));
  // In a split of the same direction the new node is a sibling; else the target is wrapped.
  const visit = (n: GridNode): GridNode => {
    if (n.kind !== "split") return n;
    const i = n.children.findIndex((c) => nodeId(c) === target);
    if (i < 0) {
      const children = n.children.map(visit);
      return children.some((c, j) => c !== n.children[j]) ? { ...n, children } : n;
    }
    if (n.dir !== dir) {
      const children = [...n.children];
      children[i] = wrap(n.children[i]);
      return { ...n, children };
    }
    const s = n.sizes[i];
    const children = [...n.children];
    const sizes = [...n.sizes];
    children.splice(first ? i : i + 1, 0, node);
    sizes.splice(i, 1, ...(first ? [s * share, s * (1 - share)] : [s * (1 - share), s * share]));
    return { ...n, children, sizes };
  };
  return settle(visit(root));
}

/**
 * Dock `panel` at `target`: beside it at an edge (a new group, splitting the
 * target's room), or into a group at `center` (as a tab before `index`, else at
 * the end), out of wherever the grid had it, as the shown tab. A panel alone in
 * its group dropped on that group's own edge or center stays where it is, as
 * does any drop on a node the grid does not have (the viewport takes no tabs).
 */
export function dockInGrid(root: GridNode, panel: PanelId, target: GridTarget): GridNode {
  const from = groupOf(root, panel);
  if (target.edge === "center") {
    const into = findNode(root, target.node);
    if (!into || into.kind !== "group") return root;
    const orig = into.panels;
    const beforeId = target.index === undefined ? null : (orig.slice(Math.max(0, target.index)).find((p) => p !== panel) ?? null);
    const placed = (g: GroupNode): GroupNode => {
      const list = g.panels.filter((p) => p !== panel);
      const at = beforeId === null || !list.includes(beforeId) ? list.length : list.indexOf(beforeId);
      list.splice(at, 0, panel);
      return { ...g, panels: list, active: panel };
    };
    if (from?.id === into.id) {
      const next = placed(into);
      if (next.panels.every((p, i) => p === orig[i]) && into.active === panel) return root;
      return settle(mapNode(root, into.id, () => next));
    }
    const out = removeFromGrid(root, panel);
    return settle(mapNode(out, into.id, (n) => (n.kind === "group" ? placed(n) : n)));
  }
  if (target.node !== ROOT_NODE && !findNode(root, target.node)) return root;
  if (from && from.id === target.node && from.panels.length === 1) return root;
  const out = removeFromGrid(root, panel);
  if (target.node !== ROOT_NODE && !findNode(out, target.node)) return root;
  const group: GroupNode = { kind: "group", id: nextId(out, "g"), panels: [panel], active: panel };
  return insertBeside(out, target.node, group, target.edge, splitShare(target.node));
}

/** Show `panel` in its group. */
export function setActiveTab(root: GridNode, panel: PanelId): GridNode {
  const g = groupOf(root, panel);
  if (!g || g.active === panel) return root;
  return mapNode(root, g.id, () => ({ ...g, active: panel })) ?? root;
}

/** A split's children's fractions, made to sum to 1. Unchanged when the count does not match. */
export function setSplitSizes(root: GridNode, splitId: string, sizes: readonly number[]): GridNode {
  const s = findNode(root, splitId);
  if (!s || s.kind !== "split" || sizes.length !== s.children.length) return root;
  const ns = normalised(sizes);
  if (ns.every((v, i) => Math.abs(v - s.sizes[i]) < 1e-9)) return root;
  return mapNode(root, splitId, () => ({ ...s, sizes: ns })) ?? root;
}

/** A fixed node's size in px, within its `min` / `max`. Unchanged for a node that is not fixed. */
export function setFixedPx(root: GridNode, id: string, px: number): GridNode {
  const n = findNode(root, id);
  if (!n || n.kind === "viewport" || !n.fixed) return root;
  const v = Math.round(clampFixed(n.fixed, px));
  if (v === n.fixed.px) return root;
  return mapNode(root, id, () => ({ ...n, fixed: { ...n.fixed!, px: v } })) ?? root;
}

/** `px` within a fixed size's bounds (and never below the collapsed strip). */
export function clampFixed(f: FixedSize, px: number): number {
  return Math.min(f.max ?? Infinity, Math.max(f.min ?? COLLAPSED_PX, px));
}

/** Collapse a group to its strip, or restore it. */
export function setGroupCollapsed(root: GridNode, id: string, collapsed: boolean): GridNode {
  const g = findNode(root, id);
  if (!g || g.kind !== "group" || !!g.collapsed === collapsed) return root;
  return mapNode(root, id, () => {
    const next: GroupNode = { ...g, collapsed };
    if (!collapsed) delete next.collapsed;
    return next;
  }) ?? root;
}

/** A group's arrangement. */
export function setGroupArrangement(root: GridNode, id: string, arrangement: GroupArrangement): GridNode {
  const g = findNode(root, id);
  if (!g || g.kind !== "group" || (g.arrangement ?? "tabs") === arrangement) return root;
  return mapNode(root, id, () => ({ ...g, arrangement })) ?? root;
}

/** A split's children, all the same size. */
export function equaliseSplit(root: GridNode, splitId: string): GridNode {
  const s = findNode(root, splitId);
  if (!s || s.kind !== "split") return root;
  return setSplitSizes(root, splitId, s.children.map(() => 1));
}

/**
 * Move the boundary between `sizes[i]` and `sizes[j]` by `delta` (in the
 * sizes' units), keeping their sum and each at least its `min`. When the two
 * cannot both keep their minimum, they share their room by their minimums.
 */
export function resizePair(sizes: readonly number[], i: number, j: number, delta: number, min: readonly number[]): number[] {
  const out = [...sizes];
  const total = sizes[i] + sizes[j];
  const mi = min[i] ?? 0;
  const mj = min[j] ?? 0;
  if (mi + mj >= total) {
    const share = mi + mj > 0 ? mi / (mi + mj) : 0.5;
    out[i] = total * share;
    out[j] = total - out[i];
    return out;
  }
  const a = Math.min(total - mj, Math.max(mi, sizes[i] + delta));
  out[i] = a;
  out[j] = total - a;
  return out;
}

// ---------------------------------------------------------------------------
// What is drawn: the grid without the groups that show no panel.
// ---------------------------------------------------------------------------

/** A split as drawn: its shown children, their fractions among themselves, and where each sits in the stored split. */
export interface ShownSplit extends Omit<SplitNode, "children"> {
  children: ShownNode[];
  slots: number[];
}

export type ShownNode = ShownSplit | GroupNode | ViewportNode;

/**
 * The grid as drawn: a group shows only its panels that `shows` (one that shows
 * none takes no room), and a split left with one child is that child. Splits
 * keep their ids, so a divider still resizes the stored split. With
 * `viewport: false` the viewport leaf takes no room either (a pop-out window,
 * which has no view): the grid is its groups alone, and the viewport alone when
 * no group shows.
 */
export function shownGrid(root: GridNode, shows: (panel: PanelId) => boolean, opts: { viewport?: boolean } = {}): ShownNode {
  const visit = (n: GridNode): ShownNode | null => {
    if (n.kind === "viewport") return opts.viewport === false ? null : n;
    if (n.kind === "group") {
      const panels = n.panels.filter(shows);
      if (panels.length === 0) return null;
      const active = n.active && panels.includes(n.active) ? n.active : panels[0];
      return panels.length === n.panels.length && active === n.active ? n : { ...n, panels, active };
    }
    const kids: ShownNode[] = [];
    const slots: number[] = [];
    n.children.forEach((c, i) => {
      const v = visit(c);
      if (v) {
        kids.push(v);
        slots.push(i);
      }
    });
    if (kids.length === 0) return null;
    if (kids.length === 1) return kids[0];
    const sum = slots.reduce((a, i) => a + n.sizes[i], 0);
    return { ...n, children: kids, sizes: slots.map((i) => (sum > 0 ? n.sizes[i] / sum : 1 / slots.length)), slots };
  };
  return visit(root) ?? (DEFAULT_GRID as ViewportNode);
}

// ---------------------------------------------------------------------------
// Geometry.
// ---------------------------------------------------------------------------

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

// ---------------------------------------------------------------------------
// Reading a stored grid.
// ---------------------------------------------------------------------------

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const posNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

/** A stored fixed size, or none when it is not one. */
function readFixed(raw: unknown): FixedSize | undefined {
  if (!isObj(raw) || !posNum(raw.px)) return undefined;
  const f: FixedSize = { px: raw.px };
  if (posNum(raw.min)) f.min = raw.min;
  if (posNum(raw.max)) f.max = raw.max;
  if (f.min !== undefined && f.max !== undefined && f.min > f.max) delete f.max;
  f.px = clampFixed(f, f.px);
  return f;
}

/**
 * A stored grid made safe to use: malformed nodes are dropped, a panel listed
 * twice is kept where it is first, ids are made unique, sizes are made to sum
 * to 1, and the tree is tidied. Anything without exactly one viewport leaf is
 * the default grid.
 */
export function normaliseGrid(raw: unknown): GridNode {
  let viewports = 0;
  const seenPanels = new Set<PanelId>();
  const seenIds = new Set<string>();
  const fresh: { node: { id: string }; prefix: "g" | "s" }[] = [];
  const claim = (node: { id: string }, raw: unknown, prefix: "g" | "s") => {
    if (typeof raw === "string" && raw && raw !== VIEWPORT_NODE && raw !== ROOT_NODE && !seenIds.has(raw)) {
      seenIds.add(raw);
      node.id = raw;
    } else fresh.push({ node, prefix });
  };
  const visit = (n: unknown, depth: number): GridNode | null => {
    if (!isObj(n) || depth > 32) return null;
    if (n.kind === "viewport") {
      viewports++;
      return { kind: "viewport" };
    }
    if (n.kind === "group") {
      const panels = (Array.isArray(n.panels) ? n.panels : []).filter(
        (p): p is string => typeof p === "string" && !seenPanels.has(p),
      );
      const unique = [...new Set(panels)];
      unique.forEach((p) => seenPanels.add(p));
      if (unique.length === 0) return null;
      const active = typeof n.active === "string" && unique.includes(n.active) ? n.active : unique[0];
      const g: GroupNode = { kind: "group", id: "", panels: unique, active };
      const fixed = readFixed(n.fixed);
      if (fixed) g.fixed = fixed;
      if (n.collapsed === true) g.collapsed = true;
      if (n.arrangement === "stack" || n.arrangement === "auto") g.arrangement = n.arrangement;
      claim(g, n.id, "g");
      return g;
    }
    if (n.kind === "split" && (n.dir === "row" || n.dir === "col") && Array.isArray(n.children)) {
      const rawSizes = Array.isArray(n.sizes) ? n.sizes : [];
      const kids: GridNode[] = [];
      const sizes: number[] = [];
      n.children.forEach((c, i) => {
        const v = visit(c, depth + 1);
        if (!v) return;
        kids.push(v);
        const s = rawSizes[i];
        sizes.push(typeof s === "number" && Number.isFinite(s) && s > 0 ? s : NaN);
      });
      if (kids.length === 0) return null;
      const even = sizes.some((s) => Number.isNaN(s)) || rawSizes.length !== n.children.length;
      const sp: SplitNode = { kind: "split", id: "", dir: n.dir, children: kids, sizes: even ? kids.map(() => 1) : sizes };
      const fixed = readFixed(n.fixed);
      if (fixed) sp.fixed = fixed;
      claim(sp, n.id, "s");
      return sp;
    }
    return null;
  };
  const root = visit(raw, 0);
  if (!root || viewports !== 1) return DEFAULT_GRID;
  for (const f of fresh) {
    let k = 1;
    while (seenIds.has(`${f.prefix}${k}`)) k++;
    f.node.id = `${f.prefix}${k}`;
    seenIds.add(f.node.id);
  }
  const fix = (n: GridNode): GridNode => (n.kind === "split" ? { ...n, children: n.children.map(fix), sizes: normalised(n.sizes) } : n);
  return settle(fix(root));
}
