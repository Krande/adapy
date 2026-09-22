// WHICH PUBLISHED SPINE COVERS A NODE, and whether it is already merged.
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
import { buildHierarchy, flattenVisible, type Hierarchy } from "../../assets/hierarchy";
import { resolveCollection } from "../../assets/resolve";
import { canFetchSpine, rowSpineState, spineCoverage, spineMerged, spineRootedAt, type SpineSource } from "../../assets/spines";
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

const NOTHING_LOADED: ReadonlyMap<string, string> = new Map();
const AREA_LOADED: ReadonlyMap<string, string> = new Map([[AREA, REVISION]]);

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

test("a level the index advertises can be expanded before its spine is in", () => {
  const h = forest(indexRows());
  const res = areaResolution();
  const cover = spineCoverage(h, (id) => spineRootedAt(res, id));
  const level = h.byId.get(LEVELS[0])?.data;
  assert.equal(canFetchSpine(level, cover.get(LEVELS[0]) ?? null, NOTHING_LOADED), true);

  const rows = flattenVisible(h, new Set([AREA]), {
    expandable: (id) => canFetchSpine(h.byId.get(id)?.data, cover.get(id) ?? null, NOTHING_LOADED),
  });
  const levelRow = rows.find((r) => r.id === LEVELS[0]);
  assert.ok(levelRow);
  assert.equal(levelRow!.hasChildren, true, "a branch with a covering spine must offer an expansion");
});

test("once the area spine is merged the leaves under it offer nothing", () => {
  const h = forest(spineRows());
  const res = areaResolution();
  const cover = spineCoverage(h, (id) => spineRootedAt(res, id));
  const rows = flattenVisible(h, new Set([AREA, ...LEVELS, "frame-0"]), {
    expandable: (id) => canFetchSpine(h.byId.get(id)?.data, cover.get(id) ?? null, AREA_LOADED),
  });
  const leaf = rows.find((r) => r.id === "member-0");
  assert.ok(leaf);
  assert.equal(leaf!.hasChildren, false, "a leaf must never sprout a twisty");
  const levelRow = rows.find((r) => r.id === LEVELS[0]);
  assert.equal(levelRow?.hasChildren, true, "the level draws its twisty from the rows it now has");
  assert.equal(canFetchSpine(h.byId.get(LEVELS[0])?.data, cover.get(LEVELS[0]) ?? null, AREA_LOADED), false);
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

test("a spine at a new revision is a different document and is re-read", () => {
  const idx = foldListing([K(AREA, "20260915T090000Z", MANIFEST_FILENAME), K(AREA, "20260915T090000Z", HIERARCHY_FILENAME)]);
  const res = resolveCollection(idx, COLL, { kind: "latest" });
  const h = forest(spineRows());
  const cover = spineCoverage(h, (id) => spineRootedAt(res, id));
  const stale = new Map([[AREA, REVISION]]);
  assert.equal(spineMerged(cover.get(LEVELS[0]) ?? null, stale), false);
  // ...but a leaf still offers nothing.
  assert.equal(canFetchSpine(h.byId.get("member-0")?.data, cover.get("member-0") ?? null, stale), false);
  assert.equal(canFetchSpine(h.byId.get(LEVELS[0])?.data, cover.get(LEVELS[0]) ?? null, stale), true);
});

// --------------------------------------------------------------------------- //
// rowSpineState: the promise on the row, and what it costs
// --------------------------------------------------------------------------- //

test("a row states the spine it is waiting on, not the id it was asked about", () => {
  const h = forest(indexRows());
  const res = areaResolution();
  const cover = spineCoverage(h, (id) => spineRootedAt(res, id));
  const ask = (id: string, loading: ReadonlySet<string>, errors: ReadonlyMap<string, string>) =>
    rowSpineState({
      node: h.byId.get(id)?.data,
      hasChildren: h.childrenOf(id).length > 0,
      source: cover.get(id) ?? null,
      loaded: NOTHING_LOADED,
      loading,
      errors,
    });

  // The area's file is in flight; the level inside it is what the user clicked.
  const inFlight = ask(LEVELS[0], new Set([AREA]), new Map());
  assert.equal(inFlight.loading, true);
  assert.equal(inFlight.error, null);
  assert.equal(inFlight.deadEnd, false);

  const failed = ask(LEVELS[0], new Set(), new Map([[AREA, "404 Not Found"]]));
  assert.equal(failed.loading, false);
  assert.equal(failed.error, "404 Not Found");
});

test("a merged spine goes quiet again for every row it contributed", () => {
  const h = forest(indexRows());
  const res = areaResolution();
  const cover = spineCoverage(h, (id) => spineRootedAt(res, id));
  const quiet = rowSpineState({
    node: h.byId.get(LEVELS[0])?.data,
    hasChildren: true,
    source: cover.get(LEVELS[0]) ?? null,
    loaded: AREA_LOADED,
    loading: new Set([AREA]),
    errors: new Map([[AREA, "404 Not Found"]]),
  });
  assert.equal(quiet.loading, false);
  assert.equal(quiet.error, null);
});

test("a branch nothing covers is a dead end once explored; a leaf never is", () => {
  const h = forest(indexRows());
  const cover = spineCoverage(h, () => null);
  const level = h.byId.get(LEVELS[0])?.data;
  const none = cover.get(LEVELS[0]) ?? null;

  const state = rowSpineState({
    node: level,
    hasChildren: false,
    source: none,
    loaded: NOTHING_LOADED,
    loading: new Set(),
    errors: new Map(),
  });
  assert.equal(state.deadEnd, true, "leaf:false, nothing covers it, no children held -> dead end");

  const leafState = rowSpineState({
    node: h.byId.get(AREA)?.data,
    hasChildren: true,
    source: none,
    loaded: NOTHING_LOADED,
    loading: new Set(),
    errors: new Map(),
  });
  assert.equal(leafState.deadEnd, false, "it has children, so it is not a dead end");
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
