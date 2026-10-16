import assert from "node:assert/strict";
import { test } from "node:test";

import type { EquipmentGlyph, LoadScenario } from "@/extensions/design_and_analysis_extension";
import { useFemConceptsStore } from "@/state/femConceptsStore";
import {
  describeRef,
  loadsGeneratedBy,
  refsOfKind,
  summarizeSelection,
  toggleRef,
  type FemConceptData,
  type FemConceptRef,
} from "@/utils/fem/femSelection";

// A selected FEM concept is a reference into the store's arrays, described by the Selected Object
// Info panel. These pin what the panel says, what a multi-selection adds up to, and that a
// selection never outlives the data it points into.

const trafo: EquipmentGlyph = {
  name: "trafo",
  mass: 20_000,
  origin: [10, 0, 0],
  axes: [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ],
  box_min: [-1, -1, 0],
  box_max: [1, 1, 2],
  cog: [10, 0, 1],
  load_cases: ["Selfweight"],
};

const selfweight: LoadScenario = {
  name: "Selfweight",
  kind: "case",
  loads: [
    { type: "accel", direction: [0, 0, -1], magnitude: 9.81 },
    { name: "trafo", type: "line", position: [9, 0, 0], end_position: [11, 0, 0], direction: [0, 0, -1], magnitude: 1000, generated_by: "trafo" },
    { name: "PL1", type: "point", position: [0, 0, 0], direction: [0, 0, -1], magnitude: 5000 },
  ],
};

const data = {
  masses: [{ name: "M1", position: [0, 0, 0], mass: 10_000 }],
  bcs: [{ name: "Sp_NE", positions: [[1, 2, 3], [4, 5, 6]], dofs: [2, 3], bc_type: "fixed" }],
  constraints: [],
  scenarios: [selfweight],
  equipment: [trafo],
} satisfies FemConceptData;

const row = (d: ReturnType<typeof describeRef>, label: string) => d?.rows.find(([k]) => k === label)?.[1];

test("a support says which dofs it holds and which it leaves free, and where the clicked node is", () => {
  const d = describeRef(data, { kind: "bc", index: 0, node: 1 });
  assert.equal(d?.name, "Sp_NE");
  assert.equal(row(d, "Restrained"), "dy, dz");
  assert.equal(row(d, "Free"), "dx, rx, ry, rz");
  assert.equal(row(d, "Node position (m)"), "(4, 5, 6)");
});

test("a generated line load names its equipment and its force", () => {
  const d = describeRef(data, { kind: "load", scenario: 0, index: 1 });
  assert.equal(d?.kind, "Line load");
  assert.equal(row(d, "Generated from"), "equipment trafo");
  assert.equal(row(d, "Length (m)"), "2");
  assert.equal(row(d, "Force (kN)"), "(0, 0, -2)");
});

test("an equipment reports its mass in kg and tonne, size and load cases", () => {
  const d = describeRef(data, { kind: "equipment", index: 0 });
  assert.equal(row(d, "Mass"), "20,000 kg (20 t)");
  assert.equal(row(d, "Size x, y, z (m)"), "2 × 2 × 2");
  assert.equal(row(d, "Load cases"), "Selfweight");
});

test("a reference past the data describes nothing", () => {
  assert.equal(describeRef(data, { kind: "mass", index: 3 }), null);
  assert.equal(describeRef(data, { kind: "load", scenario: 1, index: 0 }), null);
});

test("a multi-selection sums mass with its COG and the loads' resultant", () => {
  const s = summarizeSelection(data, [
    { kind: "mass", index: 0 },
    { kind: "equipment", index: 0 },
    { kind: "load", scenario: 0, index: 1 },
    { kind: "load", scenario: 0, index: 2 },
    { kind: "load", scenario: 0, index: 0 }, // the acceleration: counted, not a force
  ]);
  assert.equal(s.mass, 30);
  assert.deepEqual(s.cog!.map((v) => +v.toFixed(6)), [+(200 / 30).toFixed(6), 0, +(20 / 30).toFixed(6)]);
  assert.deepEqual(s.force, [0, 0, -7000]);
  assert.deepEqual(
    s.counts.map((c) => [c.kind, c.count]),
    [["Point mass", 1], ["Equipment", 1], ["Line load", 1], ["Point load", 1], ["Acceleration field", 1]],
  );
});

test("select-all of loads takes what the scenario shows, and an equipment finds the loads it became", () => {
  assert.equal(refsOfKind(data, "load", { scenario: 0 }).length, 3);
  assert.deepEqual(
    refsOfKind(data, "load", { scenario: 0, showGeneratedLoads: false }).map((r) => (r as { index: number }).index),
    [0, 2],
  );
  assert.deepEqual(loadsGeneratedBy(data, 0, "trafo"), [{ kind: "load", scenario: 0, index: 1 }]);
});

test("a second additive pick of the same concept takes it out again", () => {
  const a: FemConceptRef = { kind: "mass", index: 0 };
  const b: FemConceptRef = { kind: "bc", index: 0, node: 1 };
  assert.deepEqual(toggleRef(toggleRef([a], b), a), [b]);
});

test("the store drops a selection when the data it indexes is replaced, or a load's scenario changes", () => {
  const store = useFemConceptsStore.getState();
  store.setData({ ...data, masses: [...data.masses], scenarios: [selfweight] });
  store.setSelectedScenario(0);
  store.selectConcepts([{ kind: "mass", index: 0 }, { kind: "load", scenario: 0, index: 2 }]);
  assert.equal(useFemConceptsStore.getState().selection.length, 2);

  store.selectConcept({ kind: "equipment", index: 0 }, true);
  assert.equal(useFemConceptsStore.getState().selection.length, 3, "additive adds");
  store.selectConcept({ kind: "equipment", index: 0 });
  assert.deepEqual(useFemConceptsStore.getState().selection, [{ kind: "equipment", index: 0 }], "a plain pick replaces");

  store.selectConcepts([{ kind: "load", scenario: 0, index: 2 }], true);
  store.setSelectedScenario(-1);
  assert.deepEqual(useFemConceptsStore.getState().selection, [{ kind: "equipment", index: 0 }], "the load's arrow is gone");

  store.setData({ ...data, masses: [...data.masses] });
  assert.deepEqual(useFemConceptsStore.getState().selection, [], "new data, new indices");
});
