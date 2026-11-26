// showComplementCells (Show complement, Alt+Q) at the store level: swaps hiddenCellIds /
// hiddenRefIds for their complement among the builder's own cells and refs, clears the
// selection, and a double call is the identity. visibilityActions.test.ts already proves the
// same guarantee through its own stand-in BuilderVisibilityHost; this exercises the real store
// action itself.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");

const st = () => useCellBuilderStore.getState();
const box = { origin: [0, 0, 0] as [number, number, number], size: [1, 1, 1] as [number, number, number] };
const doc = { schema_version: "1.1", grid: {}, blueprint: {}, spaces: [], equipments: [] } as unknown as ProceduralDoc;

/** A model with two cells and one ref from a plugin provider. */
function withModel(run: () => void): void {
  st().open("m", "Model", 1, doc);
  useCellBuilderStore.setState({
    cells: {
      a: { id: "a", name: "A", kind: "cell", ...box },
      b: { id: "b", name: "B", kind: "cell", ...box },
    },
  } as never);
  const off = registerBuilderRefs({
    owner: "r",
    project: () => [{ key: "0", name: "Load", kindLabel: "Load", ...box }],
  });
  try {
    run();
  } finally {
    off();
    st().close();
  }
}

test("showComplementCells swaps hiddenCellIds/hiddenRefIds for their complement, and clears the selection", () => {
  withModel(() => {
    st().hideCells(["a"]);
    st().setSelection({ kind: "cell", cellId: "b" });
    st().showComplementCells();
    assert.deepEqual(st().hiddenCellIds, ["b"]);
    assert.deepEqual(st().hiddenRefIds, ["r:0"]);
    assert.equal(st().selection, null, "the selection is cleared, as hideCells does");
  });
});

test("a double call is the identity", () => {
  withModel(() => {
    st().hideCells(["a"]);
    st().showComplementCells();
    st().showComplementCells();
    assert.deepEqual(st().hiddenCellIds, ["a"]);
    assert.deepEqual(st().hiddenRefIds, []);
  });
});

test("with nothing hidden, show complement hides everything (cells and refs alike)", () => {
  withModel(() => {
    assert.deepEqual(st().hiddenCellIds, []);
    st().showComplementCells();
    assert.deepEqual(st().hiddenCellIds.sort(), ["a", "b"]);
    assert.deepEqual(st().hiddenRefIds, ["r:0"]);
  });
});
