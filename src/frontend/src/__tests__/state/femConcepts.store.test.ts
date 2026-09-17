import assert from "node:assert/strict";
import { test } from "node:test";

import { useFemConceptsStore } from "@/state/femConceptsStore";
import type { ConstraintGlyph } from "@/extensions/design_and_analysis_extension";

// The Scene > FEM tab's constraint picker holds an index into `constraints`, and
// the viewer overlay reads that index back to decide which constraint's nodes to
// draw. A stale index would point the overlay at the wrong constraint (or past
// the end of the list) after a model swap, so the selection rules are pinned here.
//
// No jsdom needed: this is a plain zustand store with no browser dependency.

const con = (name: string): ConstraintGlyph => ({
  name,
  constraint_type: "coupling",
  master_positions: [[0, 0, 0]],
  slave_positions: [
    [1, 0, 0],
    [2, 0, 0],
  ],
});

function setConstraints(constraints: ConstraintGlyph[]) {
  useFemConceptsStore.getState().setData({ masses: [], bcs: [], constraints, scenarios: [] });
}

test("constraints start empty with nothing selected", () => {
  setConstraints([]);
  const s = useFemConceptsStore.getState();
  assert.deepEqual(s.constraints, []);
  assert.equal(s.selectedConstraint, -1, "FEM concepts start hidden — the user opts in");
});

test("setData carries constraints through", () => {
  setConstraints([con("a"), con("b")]);
  const { constraints } = useFemConceptsStore.getState();
  assert.equal(constraints.length, 2);
  assert.equal(constraints[1].name, "b");
  assert.equal(constraints[0].slave_positions.length, 2);
});

test("a still-valid selection survives a reload", () => {
  setConstraints([con("a"), con("b"), con("c")]);
  useFemConceptsStore.getState().setSelectedConstraint(2);
  setConstraints([con("x"), con("y"), con("z")]);
  assert.equal(useFemConceptsStore.getState().selectedConstraint, 2);
});

test("a selection past the end of the new list falls back to none", () => {
  setConstraints([con("a"), con("b"), con("c")]);
  useFemConceptsStore.getState().setSelectedConstraint(2);
  setConstraints([con("only one")]);
  assert.equal(
    useFemConceptsStore.getState().selectedConstraint,
    -1,
    "index 2 no longer exists — the overlay must not read past the list",
  );
});

test("loading a model with no constraints clears the selection", () => {
  setConstraints([con("a")]);
  useFemConceptsStore.getState().setSelectedConstraint(0);
  setConstraints([]);
  assert.equal(useFemConceptsStore.getState().selectedConstraint, -1);
});

test("setData tolerates a payload with no constraints field", () => {
  // The GLB parse path and the FEA manifest path both call setData; a model
  // predating the constraints field must not leave `constraints` undefined,
  // because the panel and the tab gate read `.length` off it.
  setConstraints([con("a")]);
  useFemConceptsStore.getState().setData({ masses: [], bcs: [], scenarios: [] });
  assert.deepEqual(useFemConceptsStore.getState().constraints, []);
  assert.equal(useFemConceptsStore.getState().selectedConstraint, -1);
});

test("the constraint selection is independent of the load scenario", () => {
  useFemConceptsStore.getState().setData({
    masses: [],
    bcs: [],
    constraints: [con("a"), con("b")],
    scenarios: [{ name: "LC1", kind: "case", loads: [] }],
  });
  useFemConceptsStore.getState().setSelectedConstraint(1);
  useFemConceptsStore.getState().setSelectedScenario(0);
  const s = useFemConceptsStore.getState();
  assert.equal(s.selectedConstraint, 1, "picking a scenario must not move the constraint picker");
  assert.equal(s.selectedScenario, 0);
});
