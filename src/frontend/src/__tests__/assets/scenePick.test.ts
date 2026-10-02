// From a 3D pick's name path to the Sources row: label matching, skipping to the anchor, lazy
// levels, and stopping at the deepest match.

import assert from "node:assert/strict";
import { test } from "node:test";

import { matchRow, normalizeName, pathStartBelow, resolvePickRow, splitIdSuffix, type PickTreeSource } from "../../assets/scenePick";

/** A canned forest: id -> [label, parent]. Rows under an id in `lazy` appear only once that id's
 *  children are ensured -- a level fetched on demand. */
function source(rows: Record<string, [string, string | null]>, lazy: readonly string[] = []) {
  const fetched = new Set<string>();
  const ensured: string[] = [];
  const visible = (id: string) => {
    const parent = rows[id]?.[1];
    return parent === null || parent === undefined || !lazy.includes(parent) || fetched.has(parent);
  };
  const src: PickTreeSource = {
    label: (id) => (rows[id] && visible(id) ? rows[id][0] : undefined),
    children: (id) => Object.keys(rows).filter((k) => rows[k][1] === id && visible(k)),
    ensureChildren: async (id) => {
      ensured.push(id);
      fetched.add(id);
    },
  };
  return { src, ensured };
}

const FOREST: Record<string, [string, string | null]> = {
  site: ["/SITE-1", null],
  area: ["Area 1", "site"],
  deck: ["Deck A", "area"],
  deck2: ["Deck A", "area"],
  plate: ["PL-100", "deck"],
  beam: ["bm-7", "deck"],
};

test("names are compared trimmed and without a leading slash", () => {
  assert.equal(normalizeName("  /SITE-1 "), "SITE-1");
  assert.deepEqual(splitIdSuffix("Deck A [deck2]"), { base: "Deck A", id: "deck2" });
  assert.deepEqual(splitIdSuffix("Deck A"), { base: "Deck A", id: null });
});

test("a row matches exactly first, then ignoring case, and an id suffix names the row outright", () => {
  const rows = [
    { id: "a", label: "beam" },
    { id: "b", label: "Beam" },
    { id: "c", label: "Deck A" },
    { id: "d", label: "Deck A" },
  ];
  assert.equal(matchRow(rows, "Beam")?.id, "b");
  assert.equal(matchRow(rows, "BEAM")?.id, "a");
  assert.equal(matchRow(rows, "Deck A [d]")?.id, "d");
  // An id that names no row falls back to the label.
  assert.equal(matchRow(rows, "Deck A [zz]")?.id, "c");
  assert.equal(matchRow([{ id: "x", label: "Deck A [x]" }], "Deck A")?.id, "x");
  assert.equal(matchRow(rows, "nope"), null);
});

test("path names above the anchor are skipped", () => {
  assert.equal(pathStartBelow(["SITE-1", "Area 1", "Deck A"], { id: "area", label: "Area 1" }), 2);
  assert.equal(pathStartBelow(["Deck A"], { id: "area", label: "Area 1" }), 0);
});

test("a path that starts above the anchor descends from the anchor to the picked row", async () => {
  const { src } = source(FOREST);
  const r = await resolvePickRow(src, "area", ["SITE-1", "Area 1", "Deck A", "PL-100"]);
  assert.deepEqual(r, { row: "plate", chain: ["area", "deck", "plate"], matched: 2 });
});

test("a path that starts at the anchor, with a leading slash on the anchor's own label", async () => {
  const { src } = source(FOREST);
  const r = await resolvePickRow(src, "site", ["SITE-1", "Area 1"]);
  assert.equal(r?.row, "area");
});

test("an id suffix picks the right one of two same-named siblings", async () => {
  const { src } = source({ ...FOREST, plate2: ["PL-100", "deck2"] });
  const r = await resolvePickRow(src, "area", ["Area 1", "Deck A [deck2]", "PL-100"]);
  assert.deepEqual(r?.chain, ["area", "deck2", "plate2"]);
});

test("unfetched levels are fetched on the way down, only as deep as the path goes", async () => {
  const { src, ensured } = source(FOREST, ["area", "deck"]);
  const r = await resolvePickRow(src, "area", ["Area 1", "Deck A", "bm-7"]);
  assert.equal(r?.row, "beam");
  assert.deepEqual(ensured, ["area", "deck"]);
});

test("the walk stops at the deepest match", async () => {
  const { src } = source(FOREST);
  const r = await resolvePickRow(src, "area", ["Area 1", "Deck A", "not-in-the-tree"]);
  assert.deepEqual(r, { row: "deck", chain: ["area", "deck"], matched: 1 });
});

test("with no name for the anchor, a leading viewer-only name is stepped over", async () => {
  const { src } = source(FOREST);
  const r = await resolvePickRow(src, "area", ["model-3f2a.glb", "Deck A", "PL-100"]);
  assert.equal(r?.row, "plate");
});

test("an anchor the forest does not hold resolves to nothing", async () => {
  const { src } = source(FOREST);
  assert.equal(await resolvePickRow(src, "missing", ["Area 1"]), null);
});

test("a superseded walk gives up", async () => {
  const { src } = source(FOREST);
  const r = await resolvePickRow({ ...src, alive: () => false }, "area", ["Area 1", "Deck A"]);
  assert.equal(r, null);
});
