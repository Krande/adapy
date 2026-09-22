// Reading `hierarchy.json` off the wire: unknown schemas are refused, columns
// are found by name (never by position), `leaf` accepts several spellings, and
// an absent/blank `provider` cell falls back to the slice's own provider.

import assert from "node:assert/strict";
import { test } from "node:test";

import { HIERARCHY_SCHEMA, HierarchyError, parseHierarchySlice } from "../../assets/projection";
import type { WireHierarchySlice } from "../../assets/types";

const BASE_COLS = ["id", "parent", "label", "kind", "leaf", "delivery"];

function slice(overrides: Partial<WireHierarchySlice> = {}): WireHierarchySlice {
  return {
    schema: HIERARCHY_SCHEMA,
    provider: "fixture-lines",
    collection: "plant-a",
    root: null,
    produced_at: "2026-08-25T13:55:53Z",
    depth: 1,
    cols: BASE_COLS,
    rows: [["area-1", null, "Area 1", "area", 1, "mesh"]],
    ...overrides,
  };
}

test("an unknown schema is refused rather than partially read", () => {
  assert.throws(() => parseHierarchySlice(slice({ schema: "ada.assets/hierarchy@2" })), HierarchyError);
  assert.throws(() => parseHierarchySlice(slice({ schema: "something-else" })), HierarchyError);
});

test("columns are found by NAME — shuffled order and an extra unknown column give the same result", () => {
  const inOrder = parseHierarchySlice(slice());
  const shuffled = parseHierarchySlice(
    slice({
      cols: ["kind", "delivery", "extra-future-col", "id", "leaf", "label", "parent"],
      rows: [["area", "mesh", "ignored-value", "area-1", 1, "Area 1", null]],
    }),
  );
  assert.deepEqual(shuffled.nodes, inOrder.nodes);
});

test("a row narrower or wider than the header throws", () => {
  assert.throws(
    () => parseHierarchySlice(slice({ rows: [["area-1", null, "Area 1", "area", 1]] })),
    HierarchyError,
  );
  assert.throws(
    () => parseHierarchySlice(slice({ rows: [["area-1", null, "Area 1", "area", 1, "mesh", "extra"]] })),
    HierarchyError,
  );
});

test("a missing required column is refused, named in the message", () => {
  assert.throws(() => {
    parseHierarchySlice(
      slice({
        cols: ["id", "parent", "label", "kind", "delivery"], // no `leaf`
        rows: [["area-1", null, "Area 1", "area", "mesh"]],
      }),
    );
  }, /leaf/);
});

test("leaf accepts 1, true, and their string forms", () => {
  for (const v of [1, true, "1", "true", "TRUE", " true "]) {
    const doc = slice({ rows: [["a", null, "A", "area", v, "none"]] });
    assert.equal(parseHierarchySlice(doc).nodes[0].leaf, true, JSON.stringify(v));
  }
  for (const v of [0, false, "0", "false", "", "nah"]) {
    const doc = slice({ rows: [["a", null, "A", "area", v, "none"]] });
    assert.equal(parseHierarchySlice(doc).nodes[0].leaf, false, JSON.stringify(v));
  }
});

test("delivery: empty string on the wire becomes 'none'", () => {
  const doc = slice({ rows: [["a", null, "A", "area", 1, ""]] });
  assert.equal(parseHierarchySlice(doc).nodes[0].delivery, "none");
});

test("delivery: an unrecognised value also collapses to 'none'", () => {
  const doc = slice({ rows: [["a", null, "A", "area", 1, "sculpture"]] });
  assert.equal(parseHierarchySlice(doc).nodes[0].delivery, "none");
});

test("an absent `provider` column: every node takes the slice's own provider", () => {
  const doc = slice({
    provider: "fixture-outline",
    rows: [
      ["area-1", null, "Area 1", "area", 0, "none"],
      ["level-2", "area-1", "Level 2", "level", 1, "mesh"],
    ],
  });
  const parsed = parseHierarchySlice(doc);
  assert.equal(parsed.nodes[0].provider, "fixture-outline");
  assert.equal(parsed.nodes[1].provider, "fixture-outline");
});

test("a present-but-empty provider cell also falls back to the slice's provider", () => {
  const doc = slice({
    provider: "fixture-outline",
    cols: [...BASE_COLS, "provider"],
    rows: [["area-1", null, "Area 1", "area", 0, "none", ""]],
  });
  assert.equal(parseHierarchySlice(doc).nodes[0].provider, "fixture-outline");
});

test("a non-empty provider cell overrides the slice's provider, per row", () => {
  const doc = slice({
    provider: "fixture-outline",
    cols: [...BASE_COLS, "provider"],
    rows: [
      ["area-1", null, "Area 1", "area", 0, "none", ""],
      ["level-2", "area-1", "Level 2", "level", 1, "mesh", "fixture-lines"],
    ],
  });
  const parsed = parseHierarchySlice(doc);
  assert.equal(parsed.nodes[0].provider, "fixture-outline");
  assert.equal(parsed.nodes[1].provider, "fixture-lines");
});

test("a null/empty parent cell means a root row", () => {
  const doc = slice({
    rows: [
      ["area-1", null, "Area 1", "area", 0, "none"],
      ["area-2", "", "Area 2", "area", 0, "none"],
    ],
  });
  const parsed = parseHierarchySlice(doc);
  assert.equal(parsed.nodes[0].parent, null);
  assert.equal(parsed.nodes[1].parent, null);
});

test("the rest of the slice's header fields pass through", () => {
  const parsed = parseHierarchySlice(slice({ root: "area-1", depth: 3, produced_at: "2026-08-25T13:55:53Z" }));
  assert.equal(parsed.root, "area-1");
  assert.equal(parsed.depth, 3);
  assert.equal(parsed.producedAt, "2026-08-25T13:55:53Z");
  assert.equal(parsed.collection, "plant-a");
});
