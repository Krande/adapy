// WHICH PUBLISHED SPINE COVERS A NODE, and which LEVEL of which spine holds a
// row's children (a spine is opened one level at a time).
//
// Ported from spineCoverage.test.ts. Looking a node's spine up by its own id
// answers "is a spine published AT this node", not "does a published spine
// CONTAIN this node" -- the same thing only when every clickable row is
// itself a publish root. `spineCoverage` resolves NEAREST SPINE AT OR ABOVE
// instead.
//
// In this module set a node id IS the subject directly (no ref-to-subject
// encoding the way the plugin's `=16509/38555` refs needed).

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_FILENAME, MANIFEST_FILENAME, foldListing } from "../../assets/assetIndex";
import { buildHierarchy, buildHierarchyFrom, flattenVisible, type Hierarchy } from "../../assets/hierarchy";
import { EMPTY_FOREST, mergeLevel, mergeSpine, type Forest } from "../../assets/merge";
import { resolveCollection } from "../../assets/resolve";
import {
  levelKey,
  levelOwner,
  levelWanted,
  rowLevelState,
  spineCoverage,
  spineRootedAt,
  type SpineSource,
} from "../../assets/spines";
import type { AssetNode } from "../../assets/types";

const REVISION = "20260914T173816Z";
const COLL = "plant-a";
const AREA = "area-1";
const LEVELS = ["level-1", "level-2", "level-3"];

function node(id: string, parent: string | null, kind: string, leaf: boolean): AssetNode {
  return { id, parent, label: id, kind, leaf, delivery: "none", provider: "fixture-lines" };
}

/** The collection-index rows: the area and its levels, every one `leaf: false`
 *  -- the publish's positive claim that it HAS children. */
function indexRows(): AssetNode[] {
  return [node(AREA, null, "area", false), ...LEVELS.map((z) => node(z, AREA, "level", false))];
}

/** The area's own spine: the same rows, plus what is under each level. */
function spineRows(): AssetNode[] {
  const out = indexRows();
  for (const [i, level] of LEVELS.entries()) {
    const frame = `frame-${i}`;
    out.push(node(frame, level, "frame", false));
    out.push(node(`member-${i}`, frame, "member", true));
  }
  return out;
}

function forest(rows: readonly AssetNode[]): Hierarchy<AssetNode> {
  return buildHierarchy(rows.map((n) => ({ id: n.id, parent: n.parent, data: n })));
}

const K = (s: string, r: string, f: string): string => `assets/${COLL}/${s}/${r}/${f}`;

/** A resolution where only `AREA`'s own subtree spine is published. */
function areaResolution() {
  const idx = foldListing([K(AREA, REVISION, MANIFEST_FILENAME), K(AREA, REVISION, HIERARCHY_FILENAME)]);
  return resolveCollection(idx, COLL, { kind: "latest" });
}


// --------------------------------------------------------------------------- //
// spineRootedAt / spineCoverage: nearest-at-or-above
// --------------------------------------------------------------------------- //

test("a level is covered by the spine published at the area above it", () => {
  const res = areaResolution();
  const cover = spineCoverage(forest(indexRows()), (id) => spineRootedAt(res, id));
  for (const level of LEVELS) {
    const source = cover.get(level);
    assert.ok(source, `${level} must resolve to the spine that contains it`);
    assert.equal(source!.root, AREA);
    assert.equal(source!.subject, AREA, "the id IS the subject directly — no ref transform");
    assert.equal(source!.revision, REVISION);
  }
});

test("the collection subject itself is never its own spine", () => {
  const res = areaResolution();
  assert.equal(spineRootedAt(res, COLL), null);
});

// --------------------------------------------------------------------------- //
// levels: one level of a spine per expanded row
// --------------------------------------------------------------------------- //

/** The area's first level, as the tree route's `parent=` answers it: the
 *  levels, each counting its own children in the spine. */
function areaFirstLevel(): AssetNode[] {
  return LEVELS.map((z) => ({ ...node(z, AREA, "level", false), children: 1 }));
}

/** A shallow index (the area only), then whichever levels were fetched. */
function opened(levels: readonly { parent: string; rows: AssetNode[] }[] = []): Forest {
  let f = mergeSpine(EMPTY_FOREST, [node(AREA, null, "area", false)], { subject: COLL, revision: "idx", root: null });
  for (const l of levels) f = mergeLevel(f, l.rows, { subject: AREA, revision: REVISION, parent: l.parent });
  return f;
}

function wanted(f: Forest, res: ReturnType<typeof areaResolution>, id: string, loaded: ReadonlySet<string>): boolean {
  const h = buildHierarchyFrom(f.nodes, (n) => n.parent, (n) => n);
  return levelWanted(h.byId.get(id)?.data, levelOwner(res, f.origins, id), loaded, h.childrenOf(id).length > 0);
}

const AREA_LEVEL = levelKey({ subject: AREA, revision: REVISION, node: AREA });
const LEVEL0 = levelKey({ subject: AREA, revision: REVISION, node: LEVELS[0] });

test("a subject owns its first level; a row it contributed is opened from that same spine", () => {
  const res = areaResolution();
  const f = opened([{ parent: AREA, rows: areaFirstLevel() }]);
  assert.deepEqual(levelOwner(res, f.origins, AREA), { subject: AREA, revision: REVISION, node: AREA });
  assert.deepEqual(levelOwner(res, f.origins, LEVELS[0]), { subject: AREA, revision: REVISION, node: LEVELS[0] });
  // A row of the collection index with no spine of its own and no count: nothing to open.
  assert.equal(levelOwner(res, f.origins, "nobody"), null);
});

test("a subject whose spine is not opened can be expanded, before a single child is held", () => {
  const res = areaResolution();
  const f = opened();
  assert.equal(wanted(f, res, AREA, new Set()), true);
  const h = buildHierarchyFrom(f.nodes, (n) => n.parent, (n) => n);
  const rows = flattenVisible(h, new Set(), {
    expandable: (id) => levelWanted(h.byId.get(id)?.data, levelOwner(res, f.origins, id), new Set(), h.childrenOf(id).length > 0),
  });
  assert.equal(rows.find((r) => r.id === AREA)?.hasChildren, true);
  // Opened: it now draws its twisty from the rows it has, and wants nothing more.
  assert.equal(wanted(opened([{ parent: AREA, rows: areaFirstLevel() }]), res, AREA, new Set([AREA_LEVEL])), false);
});

test("a row off a level expands by its children count, and only until that level is in", () => {
  const res = areaResolution();
  const f = opened([{ parent: AREA, rows: areaFirstLevel() }]);
  assert.equal(wanted(f, res, LEVELS[0], new Set([AREA_LEVEL])), true, "children: 1, none held -> fetch one level");
  const deeper = opened([
    { parent: AREA, rows: areaFirstLevel() },
    { parent: LEVELS[0], rows: [{ ...node("frame-0", LEVELS[0], "frame", false), children: 0 }] },
  ]);
  assert.equal(wanted(deeper, res, LEVELS[0], new Set([AREA_LEVEL, LEVEL0])), false);
  // `children: 0` on a non-leaf row: the spine says there is nothing below -- a dead end, not a fetch.
  assert.equal(wanted(deeper, res, "frame-0", new Set([AREA_LEVEL, LEVEL0])), false);
});

test("a leaf never offers a level, whatever it counts", () => {
  const res = areaResolution();
  const f = opened([{ parent: AREA, rows: [{ ...node("m", AREA, "member", true), children: 3 }] }]);
  assert.equal(wanted(f, res, "m", new Set([AREA_LEVEL])), false);
});

test("a whole-spine row carries no count, so an empty one is explored, not pending", () => {
  const res = areaResolution();
  const f = mergeSpine(EMPTY_FOREST, spineRows(), { subject: AREA, revision: REVISION, root: AREA });
  // The area has its own spine: its first level is still the authority until opened...
  assert.equal(wanted(f, res, AREA, new Set()), true);
  // ...but a row the whole spine drew with nothing under it is a real dead end.
  assert.equal(wanted(f, res, "member-0", new Set()), false);
  assert.equal(wanted(f, res, LEVELS[0], new Set()), false, "its children are held already");
});

test("the nearest spine wins, so a nested publish beats the area above it", () => {
  const idx = foldListing([
    K(AREA, REVISION, MANIFEST_FILENAME),
    K(AREA, REVISION, HIERARCHY_FILENAME),
    K(LEVELS[0], "20260915T090000Z", MANIFEST_FILENAME),
    K(LEVELS[0], "20260915T090000Z", HIERARCHY_FILENAME),
  ]);
  const res = resolveCollection(idx, COLL, { kind: "latest" });
  const cover = spineCoverage(forest(spineRows()), (id) => spineRootedAt(res, id));
  assert.equal(cover.get(LEVELS[0])?.root, LEVELS[0]);
  assert.equal(cover.get("frame-0")?.root, LEVELS[0], "a row inside the nested spine follows it");
  assert.equal(cover.get(LEVELS[1])?.root, AREA, "a level outside it still follows the area");
});

test("a nested spine owns its own children, whatever spine drew its row", () => {
  const idx = foldListing([
    K(AREA, REVISION, MANIFEST_FILENAME),
    K(AREA, REVISION, HIERARCHY_FILENAME),
    K(LEVELS[0], "20260915T090000Z", MANIFEST_FILENAME),
    K(LEVELS[0], "20260915T090000Z", HIERARCHY_FILENAME),
  ]);
  const res = resolveCollection(idx, COLL, { kind: "latest" });
  const f = opened([{ parent: AREA, rows: areaFirstLevel() }]);
  assert.deepEqual(levelOwner(res, f.origins, LEVELS[0]), { subject: LEVELS[0], revision: "20260915T090000Z", node: LEVELS[0] });
  assert.deepEqual(levelOwner(res, f.origins, LEVELS[1]), { subject: AREA, revision: REVISION, node: LEVELS[1] });
});

test("a subject at a new revision is a different document and its first level is re-read", () => {
  const idx = foldListing([K(AREA, "20260915T090000Z", MANIFEST_FILENAME), K(AREA, "20260915T090000Z", HIERARCHY_FILENAME)]);
  const res = resolveCollection(idx, COLL, { kind: "latest" });
  const f = opened([{ parent: AREA, rows: areaFirstLevel() }]);
  assert.equal(wanted(f, res, AREA, new Set([AREA_LEVEL])), true, "loaded at the old revision only");
  // A row drawn from the old revision opens from the revision it came from, not the new one.
  assert.deepEqual(levelOwner(res, f.origins, LEVELS[0]), { subject: AREA, revision: REVISION, node: LEVELS[0] });
});

// --------------------------------------------------------------------------- //
// rowLevelState: the promise on the row, and what it costs
// --------------------------------------------------------------------------- //

test("a row states the level it is waiting on", () => {
  const res = areaResolution();
  const f = opened([{ parent: AREA, rows: areaFirstLevel() }]);
  const h = buildHierarchyFrom(f.nodes, (n) => n.parent, (n) => n);
  const ask = (id: string, loading: ReadonlySet<string>, errors: ReadonlyMap<string, string>) =>
    rowLevelState({
      node: h.byId.get(id)?.data,
      hasChildren: h.childrenOf(id).length > 0,
      req: levelOwner(res, f.origins, id),
      loaded: new Set([AREA_LEVEL]),
      loading,
      errors,
    });

  const inFlight = ask(LEVELS[0], new Set([LEVEL0]), new Map());
  assert.equal(inFlight.loading, true);
  assert.equal(inFlight.error, null);
  assert.equal(inFlight.deadEnd, false);
  // Its sibling's level is not the one in flight.
  assert.equal(ask(LEVELS[1], new Set([LEVEL0]), new Map()).loading, false);

  const failed = ask(LEVELS[0], new Set(), new Map([[LEVEL0, "404 Not Found"]]));
  assert.equal(failed.loading, false);
  assert.equal(failed.error, "404 Not Found");
});

test("a loaded level goes quiet again", () => {
  const res = areaResolution();
  const f = opened([{ parent: AREA, rows: areaFirstLevel() }]);
  const quiet = rowLevelState({
    node: f.nodes.get(AREA),
    hasChildren: true,
    req: levelOwner(res, f.origins, AREA),
    loaded: new Set([AREA_LEVEL]),
    loading: new Set([AREA_LEVEL]),
    errors: new Map([[AREA_LEVEL, "404 Not Found"]]),
  });
  assert.equal(quiet.loading, false);
  assert.equal(quiet.error, null);
});

test("a branch nothing more can open is a dead end; a branch with children never is", () => {
  const h = forest(indexRows());
  const state = rowLevelState({
    node: h.byId.get(LEVELS[0])?.data,
    hasChildren: false,
    req: null,
    loaded: new Set(),
    loading: new Set(),
    errors: new Map(),
  });
  assert.equal(state.deadEnd, true, "leaf:false, no spine holds it, no children held -> dead end");

  const withKids = rowLevelState({
    node: h.byId.get(AREA)?.data,
    hasChildren: true,
    req: null,
    loaded: new Set(),
    loading: new Set(),
    errors: new Map(),
  });
  assert.equal(withKids.deadEnd, false, "it has children, so it is not a dead end");

  const counted = rowLevelState({
    node: { ...node("x", AREA, "level", false), children: 2 },
    hasChildren: false,
    req: { subject: AREA, revision: REVISION, node: "x" },
    loaded: new Set(),
    loading: new Set(),
    errors: new Map(),
  });
  assert.equal(counted.deadEnd, false, "its spine counted children still to fetch");
});

// --------------------------------------------------------------------------- //
// spineCoverage is lazy and memoised, not one eager pass
// --------------------------------------------------------------------------- //

test("spineCoverage does no work until a row is asked about", () => {
  const h = forest(spineRows());
  const res = areaResolution();
  const asked: string[] = [];
  const counting = (id: string): SpineSource | null => {
    asked.push(id);
    return spineRootedAt(res, id);
  };
  spineCoverage(h, counting);
  assert.equal(asked.length, 0, "building the lookup must not walk the forest eagerly");
});

test("visiting every row once costs one rootedAt call per row, not a walk per row", () => {
  // The questions come from the rows on screen; a walk up stops at the first
  // memoised ancestor, so asking about a whole branch top-to-bottom costs one
  // `rootedAt` call per distinct id, never more.
  const h = forest(spineRows());
  const res = areaResolution();
  const asked: string[] = [];
  const counting = (id: string): SpineSource | null => {
    asked.push(id);
    return spineRootedAt(res, id);
  };
  const cover = spineCoverage(h, counting);
  for (const id of h.order) cover.get(id);
  assert.equal(asked.length, h.order.length);
  assert.equal(new Set(asked).size, h.order.length);
});

test("asking the same row again after it is memoised costs nothing more", () => {
  const h = forest(spineRows());
  const res = areaResolution();
  const asked: string[] = [];
  const counting = (id: string): SpineSource | null => {
    asked.push(id);
    return spineRootedAt(res, id);
  };
  const cover = spineCoverage(h, counting);
  cover.get(LEVELS[0]);
  const afterFirst = asked.length;
  cover.get(LEVELS[0]);
  assert.equal(asked.length, afterFirst, "a memoised row answers without calling rootedAt again");
});

test("an id not in the forest resolves to undefined rather than throwing", () => {
  const h = forest(indexRows());
  const cover = spineCoverage(h, () => null);
  assert.equal(cover.get("not-a-real-id"), undefined);
});
