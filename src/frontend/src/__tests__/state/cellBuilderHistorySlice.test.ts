import assert from "node:assert/strict";
import { test } from "node:test";

// The undo/redo slice is testable on its own: `makeWithHistory` binds to a
// plain `set` function, so these exercise the snapshot-push rules without a
// zustand store, without the capability layer and without a DOM.
import {
  HISTORY_LIMIT,
  makeWithHistory,
  pruneSelection,
  snapshot,
} from "@/state/cellbuilder/historySlice";
import type { CellBuilderState } from "@/state/cellbuilder/state";
import type { BuilderCell, ModelSnapshot } from "@/state/cellbuilder/types";

function cell(id: string): BuilderCell {
  return {
    id,
    name: id,
    kind: "cell",
    origin: [0, 0, 0],
    size: [1, 1, 1],
    params: {},
  };
}

/** A state stub carrying only what the history slice reads. */
function stubState(over: Partial<CellBuilderState> = {}): CellBuilderState {
  return {
    cells: { c1: cell("c1") },
    loftMembers: [],
    systems: {},
    blueprintOptions: {},
    equipmentCad: false,
    designRules: "standard",
    groups: [],
    past: [],
    future: [],
    txDepth: 0,
    ...over,
  } as unknown as CellBuilderState;
}

/** Drive `withHistory` against a stub `set`, returning the merged partial. */
function applyWithHistory(
  state: CellBuilderState,
  updater: (s: CellBuilderState) => Partial<CellBuilderState>,
): Partial<CellBuilderState> {
  let out: Partial<CellBuilderState> = {};
  const set = ((p: unknown) => {
    out = typeof p === "function"
      ? (p as (s: CellBuilderState) => Partial<CellBuilderState>)(state)
      : (p as Partial<CellBuilderState>);
  }) as Parameters<typeof makeWithHistory>[0];
  makeWithHistory(set)(updater);
  return out;
}

test("a real edit pushes the pre-change snapshot and drops the redo stack", () => {
  const before: ModelSnapshot = snapshot(stubState());
  const state = stubState({ future: [before] });
  const out = applyWithHistory(state, (s) => ({
    cells: { ...s.cells, c2: cell("c2") },
    dirty: true,
  }));

  assert.equal(out.past?.length, 1);
  assert.deepEqual(out.past?.[0].cells, { c1: state.cells.c1 });
  assert.deepEqual(out.future, [], "a new edit invalidates the redo stack");
  assert.equal(out.dirty, true);
});

test("a no-op updater changes nothing and records no history", () => {
  const out = applyWithHistory(stubState(), () => ({}));
  assert.deepEqual(out, {});
});

test("inside a transaction the edit applies but pushes no snapshot of its own", () => {
  const out = applyWithHistory(stubState({ txDepth: 1 }), (s) => ({
    cells: { ...s.cells, c2: cell("c2") },
  }));
  assert.ok(out.cells?.c2, "the edit still lands");
  assert.equal(out.past, undefined, "the transaction owns the snapshot");
});

test("the undo stack is bounded — the oldest snapshot falls off", () => {
  let state = stubState();
  for (let i = 0; i < HISTORY_LIMIT + 5; i++) {
    const out = applyWithHistory(state, (s) => ({
      cells: { ...s.cells, [`c${i}`]: cell(`c${i}`) },
    }));
    state = stubState({ cells: out.cells, past: out.past, future: out.future });
  }
  assert.equal(state.past.length, HISTORY_LIMIT);
});

test("a snapshot keeps the undoable model fields only", () => {
  const snap = snapshot(stubState());
  assert.deepEqual(Object.keys(snap).sort(), [
    "blueprintOptions",
    "cells",
    "designRules",
    "equipmentCad",
    "groups",
    "loftMembers",
    "systems",
  ]);
});

test("restoring a snapshot drops a selection whose cell is gone", () => {
  const sel = { kind: "cell", cellId: "c1" } as const;
  assert.equal(pruneSelection(sel, { c1: cell("c1") }), sel);
  assert.equal(pruneSelection(sel, {}), null);
  assert.equal(pruneSelection(null, { c1: cell("c1") }), null);
});
