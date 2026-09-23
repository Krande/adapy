// `filteredGroups`, `groupFacets` and `facetOptions` are pure functions of ONE `ClashResult` --
// no per-row state, nothing a component recomputes independently. These tests pin that by
// calling each function directly against a fixed fixture and checking the answer never depends
// on anything but its arguments (same args in, same rows out, every time).
//
// See `parseClashResult.test.ts` for why the browser globals are stubbed before the dynamic
// import -- `@/state/clashCheckStore` transitively reaches `sessionStorage` at module load.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { parseClashResult, filteredGroups, groupFacets, facetOptions, jointsForGroup, memberNamesForGroup, DEFAULT_CLASH_FILTERS } =
  await import("@/state/clashCheckStore");

// Three groups: a girder/column pair (I family, matched), a box brace pair (BOX family, matched,
// external capability) and an unmatched plate pair (no applicable spec at all).
function fixtureDoc(): Record<string, unknown> {
  return {
    schema: "ada.clash/result@1",
    source_key: "models/plant-a.ifc",
    options: {},
    counts: { members: 6, beams: 4, plates: 2, joints: 3 },
    joints: [
      {
        id: "j1",
        centre: [0, 0, 0],
        members: [
          { name: "bm1", kind: "BEAM", section: "I", member_type: "GIRDER" },
          { name: "bm2", kind: "BEAM", section: "I", member_type: "COLUMN" },
        ],
        type_key: "girder-column",
        type_label: "Girder-column",
        applicable: [{ spec: "builtin.girder_gusset", priority: 10 }],
      },
      {
        id: "j2",
        centre: [1, 0, 0],
        members: [
          { name: "bm3", kind: "BEAM", section: "BOX", member_type: "BRACE" },
          { name: "bm4", kind: "BEAM", section: "BOX", member_type: "BRACE" },
        ],
        type_key: "box-box",
        type_label: "Box-box",
        applicable: [{ spec: "external.box_spec", capability: "box-pool", priority: 5 }],
      },
      {
        id: "j3",
        centre: [2, 0, 0],
        members: [
          { name: "pl1", kind: "PLATE", section: "PLATE" },
          { name: "pl2", kind: "PLATE", section: "PLATE" },
        ],
        type_key: "plate-plate",
        type_label: "Plate-plate",
        applicable: [],
      },
    ],
    groups: [
      { type_key: "girder-column", type_label: "Girder-column", count: 1, joint_ids: ["j1"], applicable: [{ spec: "builtin.girder_gusset", priority: 10 }] },
      { type_key: "box-box", type_label: "Box-box", count: 1, joint_ids: ["j2"], applicable: [{ spec: "external.box_spec", capability: "box-pool", priority: 5 }] },
      { type_key: "plate-plate", type_label: "Plate-plate", count: 1, joint_ids: ["j3"], applicable: [] },
    ],
    provenance: {},
    warnings: [],
  };
}

test("no filters returns every group, unaffected by filter-object identity", () => {
  const result = parseClashResult(fixtureDoc());
  const a = filteredGroups(result, DEFAULT_CLASH_FILTERS);
  const b = filteredGroups(result, { ...DEFAULT_CLASH_FILTERS });
  assert.equal(a.length, 3);
  assert.deepEqual(
    a.map((g) => g.typeKey),
    b.map((g) => g.typeKey),
  );
});

test("sectionFamily filter narrows to groups whose joints carry that section", () => {
  const result = parseClashResult(fixtureDoc());
  const box = filteredGroups(result, { ...DEFAULT_CLASH_FILTERS, sectionFamily: "BOX" });
  assert.deepEqual(box.map((g) => g.typeKey), ["box-box"]);
});

test("memberType filter narrows to groups whose joints carry that member type", () => {
  const result = parseClashResult(fixtureDoc());
  const girders = filteredGroups(result, { ...DEFAULT_CLASH_FILTERS, memberType: "GIRDER" });
  assert.deepEqual(girders.map((g) => g.typeKey), ["girder-column"]);
});

test("applicable=matched / unmatched split on whether the group has a generator", () => {
  const result = parseClashResult(fixtureDoc());
  const matched = filteredGroups(result, { ...DEFAULT_CLASH_FILTERS, applicable: "matched" });
  const unmatched = filteredGroups(result, { ...DEFAULT_CLASH_FILTERS, applicable: "unmatched" });
  assert.deepEqual(matched.map((g) => g.typeKey).sort(), ["box-box", "girder-column"]);
  assert.deepEqual(unmatched.map((g) => g.typeKey), ["plate-plate"]);
});

test("typeKey filter narrows to exactly that group", () => {
  const result = parseClashResult(fixtureDoc());
  const one = filteredGroups(result, { ...DEFAULT_CLASH_FILTERS, typeKey: "box-box" });
  assert.deepEqual(one.map((g) => g.typeKey), ["box-box"]);
});

test("groupFacets reads structured member fields, not the opaque typeKey string", () => {
  const result = parseClashResult(fixtureDoc());
  const facets = groupFacets(result, "girder-column");
  assert.deepEqual(facets.kinds, ["BEAM"]);
  assert.deepEqual(facets.sectionFamilies, ["I"]);
  assert.deepEqual(facets.memberTypes, ["COLUMN", "GIRDER"]);
});

test("facetOptions unions across every group in the result", () => {
  const result = parseClashResult(fixtureDoc());
  const options = facetOptions(result);
  assert.deepEqual(options.sectionFamilies, ["BOX", "I", "PLATE"]);
  assert.deepEqual(options.memberTypes, ["BRACE", "COLUMN", "GIRDER"]);
});

test("jointsForGroup / memberNamesForGroup read through jointsById consistently", () => {
  const result = parseClashResult(fixtureDoc());
  const joints = jointsForGroup(result, "box-box");
  assert.equal(joints.length, 1);
  assert.equal(joints[0].id, "j2");
  assert.deepEqual([...memberNamesForGroup(result, "box-box")].sort(), ["bm3", "bm4"]);
});
