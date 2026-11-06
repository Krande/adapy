// The scene tree, mirrored to pop-out windows (`./windowMirror`), built for
// large trees (tens of thousands of rows): a TOPIC whose messages are
//
//   * `full`  -- the whole state: the tree, every row's visibility, the selected
//                rows. Sent when a window asks (it opened, it missed something)
//                and when the TREE changes (a model loaded or unloaded, a root
//                relabelled) -- and only then. The tree goes flat (pre-order
//                columns: ids, names, parent indices, the rows' other fields),
//                which clones far faster than nested objects, and in CHUNKS of
//                `chunkSize` rows: the first in the `full` message, the rest
//                as `part` messages, each in a task of its own (`later`), so one
//                huge clone does not hold the main window up. The window builds
//                the tree once every part is in.
//   * `part`  -- the next chunk of the tree of a `full`.
//   * `delta` -- what changed since the last message: the rows whose
//                visibility changed (a code each) and the selected rows, if they
//                changed. A hide of one element in a 20 000-row tree sends a few
//                codes (the element and its ancestors), not the tree.
//
// Each tree has a revision (`rev`, unique across main windows); a `part` or a
// `delta` for another revision than the one a window holds is refused (`apply`
// returns false) and the window asks for the full state again. A `full` for the
// revision a window already holds whole skips the rebuild.
//
// Tree changes are throttled (`treeThrottleMs`, trailing): a bulk load of 60
// models sends a few full trees, not 60. Visibility and selection changes go at
// once (coalesced per tick by `hostMirror`).
//
// Limits: a full tree costs O(rows) to flatten, clone and rebuild, once per
// model-set change and per window that opens; a visibility change costs one
// O(rows) walk in the main window (to find what changed) and O(changed rows) on
// the wire -- an isolate of a small part of a large tree changes most rows, so
// its delta is close to a full visibility map (a code per row), still without
// the tree. Expand state, search and scroll stay in each window.
//
// Dependency-free: the main window's source and the pop-out's sink are given.

import type { MirrorTopic } from "./windowMirror";

/** A tree row as the mirror carries it: an id, a name, children, and any other plain fields. */
export interface MirrorTreeNode {
  id: string;
  name: string;
  children: MirrorTreeNode[];
  [field: string]: unknown;
}

/** Row visibility codes: absent from a map = shown. */
export const ROW_VISIBILITY_CODES = { shown: 0, hidden: 1, partial: 2, none: 3 } as const;
export type RowVisibilityName = keyof typeof ROW_VISIBILITY_CODES;
export const ROW_VISIBILITY_NAMES: readonly RowVisibilityName[] = ["shown", "hidden", "partial", "none"];

/** Rows `start ..` of a flattened tree, pre-order. `parents[i]` is a global row index, -1 for a model root. */
export interface FlatTreeChunk {
  start: number;
  ids: string[];
  names: string[];
  parents: number[];
  /** The row's fields other than id, name and children; null when it has none. */
  extras: (Record<string, unknown> | null)[];
}

/** The container's own row (its children are the model roots). */
export interface FlatTreeRoot {
  id: string;
  name: string;
  extra: Record<string, unknown> | null;
}

export type SceneTreeMessage =
  | {
      k: "full";
      rev: string;
      /** Null: no tree. */
      root: FlatTreeRoot | null;
      total: number;
      parts: number;
      nodes: FlatTreeChunk | null;
      /** Visibility codes of the rows that are not shown. */
      vis: Record<string, number>;
      sel: string[];
    }
  | { k: "part"; rev: string; part: number; parts: number; nodes: FlatTreeChunk }
  | { k: "delta"; rev: string; vis?: Record<string, number>; sel?: string[] };

const SKIP = new Set(["id", "name", "children"]);

function extraOf(n: MirrorTreeNode): Record<string, unknown> | null {
  let out: Record<string, unknown> | null = null;
  for (const k of Object.keys(n)) {
    if (SKIP.has(k)) continue;
    const v = n[k];
    if (v === undefined || typeof v === "function") continue;
    (out ??= {})[k] = v;
  }
  return out;
}

/** The tree below `container`, flat and pre-order (iterative: a deep tree costs no stack). */
export function flattenTree(container: MirrorTreeNode): { root: FlatTreeRoot; ids: string[]; names: string[]; parents: number[]; extras: (Record<string, unknown> | null)[] } {
  const ids: string[] = [];
  const names: string[] = [];
  const parents: number[] = [];
  const extras: (Record<string, unknown> | null)[] = [];
  const stack: [MirrorTreeNode, number][] = [];
  const top = container.children ?? [];
  for (let i = top.length - 1; i >= 0; i--) stack.push([top[i], -1]);
  while (stack.length) {
    const [n, parent] = stack.pop()!;
    const at = ids.length;
    ids.push(String(n.id));
    names.push(String(n.name ?? ""));
    parents.push(parent);
    extras.push(extraOf(n));
    const kids = n.children ?? [];
    for (let i = kids.length - 1; i >= 0; i--) stack.push([kids[i], at]);
  }
  return { root: { id: String(container.id), name: String(container.name ?? ""), extra: extraOf(container) }, ids, names, parents, extras };
}

/** Rows `start .. start + size` of a flat tree. */
function chunkOf(flat: ReturnType<typeof flattenTree>, start: number, size: number): FlatTreeChunk {
  const end = Math.min(flat.ids.length, start + size);
  return {
    start,
    ids: flat.ids.slice(start, end),
    names: flat.names.slice(start, end),
    parents: flat.parents.slice(start, end),
    extras: flat.extras.slice(start, end),
  };
}

/** The nested tree again, from its container row and its flat rows. */
export function buildTree(root: FlatTreeRoot, ids: readonly string[], names: readonly string[], parents: readonly number[], extras: readonly (Record<string, unknown> | null)[]): MirrorTreeNode {
  const container: MirrorTreeNode = { ...(root.extra ?? {}), id: root.id, name: root.name, children: [] };
  const rows: MirrorTreeNode[] = new Array(ids.length);
  for (let i = 0; i < ids.length; i++) {
    const n: MirrorTreeNode = { ...(extras[i] ?? {}), id: ids[i], name: names[i], children: [] };
    rows[i] = n;
    const p = parents[i];
    (p < 0 ? container : rows[p]).children.push(n);
  }
  return container;
}

/** The main window's scene tree. */
export interface SceneTreeSource<N extends MirrorTreeNode = MirrorTreeNode> {
  tree(): N | null;
  /** Every row's visibility code that is not shown (absent = shown). */
  visibility(tree: N): ReadonlyMap<string, number>;
  /** The selected rows. */
  selection(): readonly string[];
  /** Notifies what may have changed. Returns the unsubscribe. */
  subscribe(cb: (what: "tree" | "visibility" | "selection") => void): () => void;
}

/** A pop-out window's copy of it. */
export interface SceneTreeSink {
  /** The tree (null: none), once whole. */
  tree(tree: MirrorTreeNode | null): void;
  /** Visibility codes: `reset` replaces every row's, else only these rows change (0 = shown again). */
  visibility(changes: ReadonlyMap<string, number>, reset: boolean): void;
  selection(ids: readonly string[]): void;
}

export interface SceneTreeTopicOptions {
  /** Rows per message of a full tree. Default 5000. */
  chunkSize?: number;
  /** A tree change waits this long for more (trailing). Default 250 ms; 0 = at once. */
  treeThrottleMs?: number;
  /** Runs `fn` later (the next part, a throttled change). Default `setTimeout`. */
  later?: (fn: () => void, ms: number) => void;
}

export const SCENE_TREE_CHUNK = 5000;

const sameList = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x, i) => x === b[i]);
const toRecord = (m: ReadonlyMap<string, number>) => {
  const out: Record<string, number> = {};
  for (const [k, v] of m) out[k] = v;
  return out;
};
const toMap = (r: Record<string, number> | undefined) => new Map(Object.entries(r ?? {}));

/** The scene tree as a mirror topic: `source` read in the main window, `sink` written in a pop-out. */
export function sceneTreeTopic<N extends MirrorTreeNode>(
  id: string,
  source: SceneTreeSource<N>,
  sink: SceneTreeSink,
  opts: SceneTreeTopicOptions = {},
): MirrorTopic<SceneTreeMessage> {
  const chunkSize = Math.max(1, opts.chunkSize ?? SCENE_TREE_CHUNK);
  const throttle = opts.treeThrottleMs ?? 250;
  const later = opts.later ?? ((fn: () => void, ms: number) => void setTimeout(fn, ms));

  // --- the main window's side ---
  const epoch = Math.random().toString(36).slice(2, 10);
  let revN = 0;
  let rev = `${epoch}:0`;
  let lastTree: N | null | undefined;
  let flat: ReturnType<typeof flattenTree> | null = null;
  let parts = 0;
  let nextPart = 0;
  let sentVis = new Map<string, number>();
  let sentSel: readonly string[] = [];
  let treeDirty = false;
  let visDirty = false;
  let selDirty = false;
  let treeTimer = false;
  let partQueued = false;
  const listeners = new Set<() => void>();
  const notify = () => {
    for (const l of [...listeners]) l();
  };
  /** The next part goes in a later task: the main window breathes between chunks. */
  const queuePart = () => {
    if (partQueued) return;
    partQueued = true;
    later(() => {
      partQueued = false;
      notify();
    }, 0);
  };

  const full = (): SceneTreeMessage => {
    const tree = source.tree();
    if (tree !== lastTree || (tree && !flat)) {
      lastTree = tree;
      rev = `${epoch}:${++revN}`;
      flat = tree ? flattenTree(tree) : null;
    }
    treeDirty = visDirty = selDirty = false;
    const vis = tree ? new Map(source.visibility(tree)) : new Map<string, number>();
    sentVis = vis;
    sentSel = [...source.selection()];
    const total = flat?.ids.length ?? 0;
    parts = flat ? Math.max(1, Math.ceil(total / chunkSize)) : 0;
    nextPart = 1;
    if (parts > 1) queuePart();
    return {
      k: "full",
      rev,
      root: flat?.root ?? null,
      total,
      parts,
      nodes: flat ? chunkOf(flat, 0, chunkSize) : null,
      vis: toRecord(vis),
      sel: [...sentSel],
    };
  };

  const delta = (): SceneTreeMessage | null => {
    if (treeDirty) return full();
    if (flat && nextPart < parts) {
      const part = nextPart++;
      // More parts, or changes that waited for them: another message after this one.
      if (nextPart < parts || visDirty || selDirty) queuePart();
      return { k: "part", rev, part, parts, nodes: chunkOf(flat, part * chunkSize, chunkSize) };
    }
    const msg: { k: "delta"; rev: string; vis?: Record<string, number>; sel?: string[] } = { k: "delta", rev };
    if (visDirty) {
      visDirty = false;
      const tree = source.tree();
      const cur = tree ? new Map(source.visibility(tree)) : new Map<string, number>();
      const changed = new Map<string, number>();
      for (const [k, v] of cur) if (sentVis.get(k) !== v) changed.set(k, v);
      for (const k of sentVis.keys()) if (!cur.has(k)) changed.set(k, 0);
      sentVis = cur;
      if (changed.size) msg.vis = toRecord(changed);
    }
    if (selDirty) {
      selDirty = false;
      const sel = source.selection();
      if (!sameList(sel, sentSel)) {
        sentSel = [...sel];
        msg.sel = [...sel];
      }
    }
    return msg.vis || msg.sel ? msg : null;
  };

  // --- a pop-out window's side ---
  let have: string | null = null;
  let whole = false;
  let got = 0;
  let want = 0;
  let root: FlatTreeRoot | null = null;
  let ids: string[] = [];
  let names: string[] = [];
  let parentsIn: number[] = [];
  let extras: (Record<string, unknown> | null)[] = [];
  const put = (c: FlatTreeChunk) => {
    for (let i = 0; i < c.ids.length; i++) {
      ids[c.start + i] = c.ids[i];
      names[c.start + i] = c.names[i];
      parentsIn[c.start + i] = c.parents[i];
      extras[c.start + i] = c.extras[i];
    }
    got++;
  };
  const finish = () => {
    whole = true;
    sink.tree(root ? buildTree(root, ids, names, parentsIn, extras) : null);
    ids = [];
    names = [];
    parentsIn = [];
    extras = [];
  };

  const apply = (m: SceneTreeMessage): boolean => {
    if (!m || typeof m !== "object") return false;
    if (m.k === "full") {
      if (!(m.rev === have && whole)) {
        have = m.rev;
        whole = false;
        root = m.root;
        want = m.parts;
        got = 0;
        ids = new Array(m.total);
        names = new Array(m.total);
        parentsIn = new Array(m.total);
        extras = new Array(m.total);
        if (!m.root || !m.nodes) finish();
        else {
          put(m.nodes);
          if (got >= want) finish();
        }
      }
      sink.visibility(toMap(m.vis), true);
      sink.selection(m.sel ?? []);
      return true;
    }
    if (m.rev !== have) return false;
    if (m.k === "part") {
      if (whole) return true;
      // Parts come in order; a gap means one was missed.
      if (m.part !== got) return false;
      put(m.nodes);
      if (got >= want) finish();
      return true;
    }
    if (m.vis) sink.visibility(toMap(m.vis), false);
    if (m.sel) sink.selection(m.sel);
    return true;
  };

  return {
    id,
    snapshot: full,
    delta,
    apply,
    subscribe(cb) {
      listeners.add(cb);
      const off = source.subscribe((what) => {
        if (what === "visibility") visDirty = true;
        else if (what === "selection") selDirty = true;
        else {
          if (throttle <= 0) {
            treeDirty = true;
            cb();
            return;
          }
          if (treeTimer) return;
          treeTimer = true;
          later(() => {
            treeTimer = false;
            treeDirty = true;
            notify();
          }, throttle);
          return;
        }
        cb();
      });
      return () => {
        listeners.delete(cb);
        off();
      };
    },
  };
}
