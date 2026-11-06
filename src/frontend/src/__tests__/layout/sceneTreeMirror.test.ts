// The scene tree's mirror topic: a full tree (flat, in chunks) on request and on
// a tree change, visibility and selection as deltas otherwise; a window that
// misses a part or holds another revision asks again; a 20 000-row tree's hide
// sends a delta, not the tree.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { createWindowBus, memoryTransportHub, MAIN_WINDOW_ID } = await import("@/layout/windowBus");
const { followMirror, hostMirror, registerMirrorTopic, resetMirror, listMirrorTopics } = await import("@/layout/windowMirror");
const { sceneTreeTopic, flattenTree, buildTree } = await import("@/layout/sceneTreeMirror");
type Node = import("@/layout/sceneTreeMirror").MirrorTreeNode;
type Msg = import("@/layout/sceneTreeMirror").SceneTreeMessage;

/** A container of `models` roots, each `width` levels of `per` rows with `per` leaves below: rows = models * per * (1 + per) + models. */
function bigTree(models: number, per: number): Node {
  let n = 0;
  const roots: Node[] = [];
  for (let m = 0; m < models; m++) {
    const levels: Node[] = [];
    for (let l = 0; l < per; l++) {
      const leaves: Node[] = [];
      for (let k = 0; k < per; k++) leaves.push({ id: `r${n++}`, name: `Leaf ${k}`, children: [], model_key: `m${m}`, rangeId: String(k), node_name: "mesh" });
      levels.push({ id: `r${n++}`, name: `Level ${l}`, children: leaves, meta: `${per}` });
    }
    roots.push({ id: `r${n++}`, name: `Model ${m}`, children: levels, source_name: `model-${m}.glb`, top_name: `Model ${m}` });
  }
  return { id: "__roots__", name: "", children: roots };
}

const count = (t: Node): number => t.children.reduce((s, c) => s + 1 + count(c), 0);

/** The main window's tree, visibility and selection, as plain state with listeners. */
function fakeSource(tree: Node | null) {
  const s = {
    tree,
    vis: new Map<string, number>(),
    sel: [] as string[],
    cbs: new Set<(w: "tree" | "visibility" | "selection") => void>(),
    visReads: 0,
    emit(w: "tree" | "visibility" | "selection") {
      for (const cb of s.cbs) cb(w);
    },
  };
  return {
    s,
    source: {
      tree: () => s.tree,
      visibility: () => {
        s.visReads++;
        return s.vis;
      },
      selection: () => s.sel,
      subscribe: (cb: (w: "tree" | "visibility" | "selection") => void) => {
        s.cbs.add(cb);
        return () => void s.cbs.delete(cb);
      },
    },
  };
}

function fakeSink() {
  const k = { tree: undefined as Node | null | undefined, trees: 0, vis: new Map<string, number>(), visCalls: [] as [number, boolean][], sel: [] as readonly string[] };
  return {
    k,
    sink: {
      tree: (t: Node | null) => {
        k.tree = t;
        k.trees++;
      },
      visibility: (changes: ReadonlyMap<string, number>, reset: boolean) => {
        k.visCalls.push([changes.size, reset]);
        if (reset) k.vis.clear();
        for (const [id, c] of changes) (c === 0 ? k.vis.delete(id) : k.vis.set(id, c));
      },
      selection: (ids: readonly string[]) => void (k.sel = ids),
    },
  };
}

/** Main and a pop-out on one hub; the topic registered once (one module, two pages: one sink here), timers and ticks queued. */
function setup(tree: Node | null, opts: { chunkSize?: number; treeThrottleMs?: number } = {}) {
  resetMirror();
  const hub = memoryTransportHub();
  const c = { t: 1000 };
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), now: () => c.t, timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now: () => c.t, timers: false });
  const src = fakeSource(tree);
  const snk = fakeSink();
  const timers: (() => void)[] = [];
  registerMirrorTopic(sceneTreeTopic("t:tree", src.source, snk.sink, { ...opts, later: (fn) => void timers.push(fn) }));
  const ticks: (() => void)[] = [];
  const posted: { topic: string; data: Msg }[] = [];
  const spy = hub.connect();
  spy.subscribe((m) => void (m.type === "mirror" && posted.push({ topic: m.topic, data: m.data as Msg })));
  let requests = 0;
  spy.subscribe((m) => void (m.type === "mirror-request" && requests++));
  main.start();
  hostMirror(main, (fn) => void ticks.push(fn));
  w2.start();
  followMirror(w2);
  /** Run every queued tick and timer until none is left. */
  const drain = () => {
    for (let guard = 0; guard < 1000 && (ticks.length || timers.length); guard++) {
      while (ticks.length) ticks.shift()!();
      if (timers.length) timers.shift()!();
    }
  };
  return { main, w2, src: src.s, snk: snk.k, posted, drain, requests: () => requests };
}

test("flatten and build give the same tree back, fields and all", () => {
  const t = bigTree(2, 3);
  const f = flattenTree(t);
  assert.equal(f.ids.length, count(t));
  assert.deepEqual(buildTree(f.root, f.ids, f.names, f.parents, f.extras), t);
});

test("a pop-out gets the whole tree, its visibility and selection when it starts", () => {
  const tree = bigTree(2, 3);
  const s = setup(tree, { chunkSize: 4 });
  s.src.vis.set("r0", 1);
  s.src.sel = ["r1"];
  s.drain();
  // Re-asked after the values were set: the window asked once at start already.
  s.w2.post({ type: "mirror-request", topics: ["t:tree"] });
  s.drain();
  assert.deepEqual(s.snk.tree, tree);
  assert.deepEqual([...s.snk.vis], [["r0", 1]]);
  assert.deepEqual(s.snk.sel, ["r1"]);
  // In chunks of 4 rows: a full, then parts.
  const kinds = s.posted.map((p) => p.data.k);
  assert.equal(kinds[0], "full");
  assert.ok(kinds.includes("part"));
  // The second full was for the tree the window already held: not rebuilt.
  assert.equal(s.snk.trees, 1);
});

test("a 20 000-row tree: a hide sends a small delta, never the tree", () => {
  // 4 models x 70 levels x 70 leaves: 19 884 rows.
  const tree = bigTree(4, 70);
  const rows = count(tree);
  assert.ok(rows > 19_000, `${rows} rows`);
  const s = setup(tree);
  s.drain();
  assert.equal(count(s.snk.tree!), rows, "the whole tree arrived, in parts");
  const fullMsgs = s.posted.filter((p) => p.data.k === "full" || p.data.k === "part");
  assert.equal(fullMsgs.length, Math.ceil(rows / 5000));
  s.posted.length = 0;
  // Hide one leaf: it, its level and its model change.
  s.src.vis.set("r0", 1);
  s.src.vis.set("r70", 2);
  s.src.vis.set(tree.children[0].id, 2);
  s.src.emit("visibility");
  s.drain();
  assert.equal(s.posted.length, 1);
  const d = s.posted[0].data;
  assert.equal(d.k, "delta");
  assert.equal("nodes" in d, false);
  assert.deepEqual(d.k === "delta" ? d.vis : null, { r0: 1, r70: 2, [tree.children[0].id]: 2 });
  assert.ok(JSON.stringify(d).length < 200, "a few codes on the wire");
  assert.equal(s.snk.vis.get("r0"), 1);
  assert.equal(s.snk.trees, 1, "the tree was not sent again");
  // Shown again: a code 0 for each row, and the window's map empties.
  s.posted.length = 0;
  s.src.vis.clear();
  s.src.emit("visibility");
  s.drain();
  assert.equal(s.posted.length, 1);
  assert.equal(Object.keys((s.posted[0].data as { vis: Record<string, number> }).vis).length, 3);
  assert.equal(s.snk.vis.size, 0);
  // A selection: the ids only.
  s.posted.length = 0;
  s.src.sel = ["r5", "r6"];
  s.src.emit("selection");
  s.drain();
  assert.deepEqual(s.posted.map((p) => p.data), [{ k: "delta", rev: (s.posted[0].data as { rev: string }).rev, sel: ["r5", "r6"] }]);
  assert.deepEqual(s.snk.sel, ["r5", "r6"]);
  // Nothing changed: nothing sent.
  s.posted.length = 0;
  s.src.emit("selection");
  s.src.emit("visibility");
  s.drain();
  assert.equal(s.posted.length, 0);
});

test("a tree change sends the tree again, throttled; the window rebuilds it", () => {
  const s = setup(bigTree(1, 2), { treeThrottleMs: 250 });
  s.drain();
  assert.equal(s.snk.trees, 1);
  s.posted.length = 0;
  const next = bigTree(3, 2);
  s.src.tree = next;
  // A burst of changes (a bulk load): one full tree.
  s.src.emit("tree");
  s.src.emit("tree");
  s.src.emit("tree");
  s.drain();
  const fulls = s.posted.filter((p) => p.data.k === "full");
  assert.equal(fulls.length, 1);
  assert.equal(s.snk.trees, 2);
  assert.deepEqual(s.snk.tree, next);
  // Unloaded: no tree.
  s.src.tree = null;
  s.src.emit("tree");
  s.drain();
  assert.equal(s.snk.tree, null);
});

test("a part or a delta for a state the window does not hold makes it ask again", () => {
  const tree = bigTree(2, 3);
  const s = setup(tree, { chunkSize: 4 });
  s.drain();
  const before = s.requests();
  const topic = listMirrorTopics().find((t) => t.id === "t:tree")!;
  // Another main window's revision, or a part out of order: refused.
  assert.equal(topic.apply({ k: "delta", rev: "other:1", sel: ["x"] }), false);
  const rev = (s.posted[0].data as { rev: string }).rev;
  assert.equal(topic.apply({ k: "delta", rev, sel: ["r2"] }), true);
  assert.deepEqual(s.snk.sel, ["r2"]);
  // Over the bus: the window posts a request for the topic.
  s.main.post({ type: "mirror", topic: "t:tree", data: { k: "delta", rev: "other:2", sel: [] }, seq: 999 });
  assert.equal(s.requests(), before + 1);
  // A full whose parts arrive with a gap: refused at the gap.
  const t2 = bigTree(2, 3);
  const f = flattenTree(t2);
  const chunk = (start: number) => ({ start, ids: f.ids.slice(start, start + 4), names: f.names.slice(start, start + 4), parents: f.parents.slice(start, start + 4), extras: f.extras.slice(start, start + 4) });
  const parts = Math.ceil(f.ids.length / 4);
  assert.equal(topic.apply({ k: "full", rev: "z:1", root: f.root, total: f.ids.length, parts, nodes: chunk(0), vis: {}, sel: [] }), true);
  assert.equal(topic.apply({ k: "part", rev: "z:1", part: 2, parts, nodes: chunk(8) }), false);
});

test("core registers core:scene-tree; the pop-out's copy goes to its tree store, roots named as it names them", async () => {
  resetMirror();
  const { registerCoreMirror, resetCoreMirror } = await import("@/layout/windowMirrorCore");
  const { resetSceneActionCommands } = await import("@/layout/sceneActions");
  const { SCENE_TREE_TOPIC, coreSceneTreeSink } = await import("@/layout/sceneTreeMirrorCore");
  const { useTreeViewStore } = await import("@/state/treeViewStore");
  const { useSceneMirrorStore } = await import("@/state/sceneMirrorStore");
  resetCoreMirror();
  resetSceneActionCommands();
  registerCoreMirror();
  assert.ok(listMirrorTopics().some((t) => t.id === SCENE_TREE_TOPIC));
  const sink = coreSceneTreeSink();
  useTreeViewStore.setState({ rootLabelMode: "id" });
  sink.tree({ id: "__roots__", name: "", children: [{ id: "m", name: "Site", top_name: "Site", source_label: "site.glb", children: [] }] });
  assert.equal(useTreeViewStore.getState().treeData?.children[0].name, "site.glb");
  sink.visibility(new Map([["m", 1]]), true);
  assert.equal(useSceneMirrorStore.getState().visibility.get("m"), 1);
  sink.visibility(new Map([["m", 0]]), false);
  assert.equal(useSceneMirrorStore.getState().visibility.has("m"), false);
  sink.selection(["m"]);
  assert.deepEqual(useSceneMirrorStore.getState().treeSelection, ["m"]);
  sink.tree(null);
  assert.equal(useTreeViewStore.getState().treeData, null);
  useTreeViewStore.setState({ rootLabelMode: "name" });
});

test("the selected rows: the rows a tree selection chose while it stands, else the picked ranges' rows", async () => {
  const { noteTreeSelection, treeSelectionIds, rowsById } = await import("@/utils/tree_view/treeSelection");
  const { useTreeViewStore } = await import("@/state/treeViewStore");
  const { useSelectedObjectStore } = await import("@/state/useSelectedObjectStore");
  const tree = { id: "__roots__", name: "", children: [{ id: "m", name: "M", children: [{ id: "a", name: "A", model_key: "k", rangeId: "7", node_name: "mesh", children: [] }] }] };
  useTreeViewStore.getState().setTreeData(tree as never);
  useSelectedObjectStore.setState({ selectedObjects: new Map() });
  noteTreeSelection(["m"]);
  assert.deepEqual(treeSelectionIds(), ["m"]);
  // A pick since: the picked range's row.
  const mesh = { unique_key: "k" };
  useSelectedObjectStore.setState({ selectedObjects: new Map([[mesh as never, new Set(["7"])]]) });
  assert.deepEqual(treeSelectionIds(), ["a"]);
  assert.deepEqual(rowsById(["a", "nope", "__roots__"]).map((r) => r.id), ["a"]);
  useTreeViewStore.getState().clearTreeData();
  useSelectedObjectStore.setState({ selectedObjects: new Map() });
});
