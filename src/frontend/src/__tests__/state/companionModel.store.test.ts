import assert from "node:assert/strict";
import { test } from "node:test";

import {
  companionSourceName,
  topologyCompanions,
  useCompanionModelStore,
} from "@/state/companionModelStore";

// A companion is a procedural model present in the scene but NOT the one being
// edited. The cellbuilder edits one document — a real constraint — but nothing
// required the scene to hold only one model.

const model = (id: string, over: Partial<Parameters<typeof useCompanionModelStore.getState>[0]> = {}) => ({
  modelId: id,
  name: `decks/${id}`,
  cells: [],
  rep: "topology" as const,
  offsetX: 0,
  latestGlbKey: null,
  ...over,
});

function reset() {
  useCompanionModelStore.getState().clear();
}

test("a companion can be added and removed", () => {
  reset();
  const s = () => useCompanionModelStore.getState();
  s().add(model("a"));
  assert.equal(Object.keys(s().companions).length, 1);
  s().remove("a");
  assert.equal(Object.keys(s().companions).length, 0);
});

test("removing an unknown companion is a no-op, not a crash", () => {
  reset();
  const before = useCompanionModelStore.getState().companions;
  useCompanionModelStore.getState().remove("nope");
  assert.equal(useCompanionModelStore.getState().companions, before, "state identity must not churn");
});

test("adding the same model twice replaces rather than duplicates", () => {
  // Otherwise its cells would render twice, at two offsets.
  reset();
  const s = () => useCompanionModelStore.getState();
  s().add(model("a", { offsetX: 0 }));
  s().add(model("a", { offsetX: 40 }));
  assert.equal(Object.keys(s().companions).length, 1);
  assert.equal(s().companions.a.offsetX, 40);
});

test("only topology companions are drawn by the renderer", () => {
  // simulation/detail load as ordinary scene sources; drawing their cells too
  // would show one model twice.
  reset();
  const s = () => useCompanionModelStore.getState();
  s().add(model("a"));
  s().add(model("b", { rep: "simulation" }));
  assert.deepEqual(topologyCompanions(s()).map((c) => c.modelId), ["a"]);
});

test("setRep on an unknown id changes nothing", () => {
  reset();
  const before = useCompanionModelStore.getState().companions;
  useCompanionModelStore.getState().setRep("nope", "detail");
  assert.equal(useCompanionModelStore.getState().companions, before);
});

test("setting the rep it already has does not churn state", () => {
  // The renderer rebuilds on identity change; a no-op write would clear and
  // redraw every companion for nothing.
  reset();
  const s = () => useCompanionModelStore.getState();
  s().add(model("a"));
  const before = s().companions;
  s().setRep("a", "topology");
  assert.equal(s().companions, before);
});

test("the scene source name distinguishes simulation from detail", () => {
  // They are separate sources so switching between them cannot leave both in
  // the scene under one name.
  assert.equal(companionSourceName("decks/a", "simulation"), "procedural:decks/a");
  assert.equal(companionSourceName("decks/a", "detail"), "procedural-detail:decks/a");
  assert.notEqual(
    companionSourceName("decks/a", "simulation"),
    companionSourceName("decks/a", "detail"),
  );
});

test("the source name is derived from the model, so two never collide", () => {
  assert.notEqual(companionSourceName("a", "simulation"), companionSourceName("b", "simulation"));
});
