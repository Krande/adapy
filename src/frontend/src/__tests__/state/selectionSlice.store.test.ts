// Multi-select with refs: selectedRefIds beside selectedCellIds, toggleRefSelection as
// the ref analogue of toggleCellSelection, and selectItems -- the mixed-ids entry point a box select
// or a Model panel click uses -- which can replace ("only"), join ("add") or flip ("toggle")
// membership across both sets at once, always landing the primary on the most recently given id
// that is still selected. selectCells / setSelection / toggleCellSelection keep their existing
// semantics (this is the regression pin for that promise).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");

const st = () => useCellBuilderStore.getState();
const REF_A = "sel-test:a";
const REF_B = "sel-test:b";

const doc = {
  schema_version: "1.1", grid: {}, blueprint: {},
  spaces: [{ NAME: "S1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }, { NAME: "S2", X: 5, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }],
  equipments: [],
} as unknown as ProceduralDoc;

function withModel(run: () => void): void {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "sel-test",
    project: () => [
      { key: "a", name: "a", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] },
      { key: "b", name: "b", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] },
    ],
  });
  try {
    run();
  } finally {
    off();
    st().close();
  }
}
const cellId = (name: string) => Object.values(st().cells).find((c) => c.name === name)!.id;

test("toggleRefSelection adds/removes a ref, leaving selectedCellIds untouched either way", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().toggleCellSelection(s1);
    assert.deepEqual(st().selectedCellIds, [s1]);
    st().toggleRefSelection(REF_A);
    assert.deepEqual(st().selectedRefIds, [REF_A]);
    assert.deepEqual(st().selectedCellIds, [s1], "the cell set is untouched by a ref toggle");
    assert.deepEqual(st().selection, { kind: "ref", refId: REF_A }, "the ref becomes primary: most recently touched");
    st().toggleRefSelection(REF_A); // remove it again
    assert.deepEqual(st().selectedRefIds, []);
    // Emptying one set falls back to the primary of whichever OTHER set still has items,
    // rather than clearing the selection outright -- the cell set did not just change.
    assert.deepEqual(st().selection, { kind: "cell", cellId: s1 }, "falls back to the still-selected cell");
    assert.deepEqual(st().selectedCellIds, [s1], "the cell set survives the ref set going to empty");
  });
});

test("toggleRefSelection ignores an unknown ref id", () => {
  withModel(() => {
    st().toggleRefSelection("no-such:ref");
    assert.deepEqual(st().selectedRefIds, []);
  });
});

test("setSelection resets both sets to just the one picked item (its own kind's set to [id], the other's to [])", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().toggleCellSelection(s1);
    st().toggleRefSelection(REF_A);
    assert.deepEqual(st().selectedCellIds, [s1]);
    assert.deepEqual(st().selectedRefIds, [REF_A]);

    st().setSelection({ kind: "ref", refId: REF_B });
    assert.deepEqual(st().selectedRefIds, [REF_B], "a ref pick resets the ref set to just itself");
    assert.deepEqual(st().selectedCellIds, [], "and clears the cell set -- a ref is no cell");

    const s2 = cellId("S2");
    st().setSelection({ kind: "cell", cellId: s2 });
    assert.deepEqual(st().selectedCellIds, [s2]);
    assert.deepEqual(st().selectedRefIds, [], "a cell pick clears the ref set too");

    st().setSelection(null);
    assert.deepEqual(st().selectedCellIds, []);
    assert.deepEqual(st().selectedRefIds, []);
  });
});

test("selectItems 'only' replaces both sets with a mix, primary = the last given id", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const s2 = cellId("S2");
    st().selectItems([s1, REF_A, s2], "only");
    assert.deepEqual(st().selectedCellIds.sort(), [s1, s2].sort());
    assert.deepEqual(st().selectedRefIds, [REF_A]);
    assert.deepEqual(st().selection, { kind: "cell", cellId: s2 }, "s2 is last in the list");

    st().selectItems([REF_B], "only");
    assert.deepEqual(st().selectedCellIds, [], "'only' clears what is not in the new list");
    assert.deepEqual(st().selectedRefIds, [REF_B]);
    assert.deepEqual(st().selection, { kind: "ref", refId: REF_B });

    st().selectItems([], "only");
    assert.deepEqual(st().selectedCellIds, []);
    assert.deepEqual(st().selectedRefIds, []);
    assert.equal(st().selection, null, "an empty list clears the selection");
  });
});

test("selectItems 'add' joins the existing sets instead of replacing them", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().selectItems([s1], "only");
    st().selectItems([REF_A], "add");
    assert.deepEqual(st().selectedCellIds, [s1], "the earlier cell survives an 'add' of a ref");
    assert.deepEqual(st().selectedRefIds, [REF_A]);
    assert.deepEqual(st().selection, { kind: "ref", refId: REF_A });
  });
});

test("selectItems 'toggle' flips membership per id, mixed cells and refs in one call", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const s2 = cellId("S2");
    st().selectItems([s1, REF_A], "only");
    st().selectItems([s1, REF_B], "toggle"); // s1 off, REF_A untouched, REF_B on
    assert.deepEqual(st().selectedCellIds, [], "s1 toggled off");
    assert.deepEqual(st().selectedRefIds.sort(), [REF_A, REF_B].sort());
    // s2 alone, toggled on: the last id in a single-id call is itself the primary when it ends up
    // selected.
    st().selectItems([s2], "toggle");
    assert.deepEqual(st().selection, { kind: "cell", cellId: s2 });
  });
});

test("selectItems falls back to whatever else is still selected when a toggle removes the given (and so far only) primary", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().selectItems([s1, REF_A], "only"); // REF_A ends up primary (last in the list)
    assert.deepEqual(st().selection, { kind: "ref", refId: REF_A });
    st().selectItems([REF_A], "toggle"); // remove the primary; s1 is still selected
    assert.deepEqual(st().selectedRefIds, []);
    assert.deepEqual(st().selectedCellIds, [s1]);
    assert.deepEqual(st().selection, { kind: "cell", cellId: s1 }, "falls back to what remains, not to null");
  });
});

test("selectItems drops an unknown id silently, keeping the rest", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().selectItems([s1, "nonsense:id", REF_A], "only");
    assert.deepEqual(st().selectedCellIds, [s1]);
    assert.deepEqual(st().selectedRefIds, [REF_A]);
  });
});

// "add"/"toggle" must not drop a multi-item G that is already armed on the set
// being grown or trimmed -- a plain "only" pick still resets it, as before.
test("selectItems keeps gizmoMode on add/toggle, resets it only for only", () => {
  withModel(() => {
    const s1 = cellId("S1");
    const s2 = cellId("S2");
    st().selectItems([s1, s2], "only");
    useCellBuilderStore.setState({ gizmoMode: "translate" } as never);

    st().selectItems([REF_A], "add");
    assert.equal(st().gizmoMode, "translate", "add keeps it armed");

    st().selectItems([s1], "toggle");
    assert.equal(st().gizmoMode, "translate", "toggle keeps it armed");

    st().selectItems([s2], "only");
    assert.equal(st().gizmoMode, "none", "only still resets it, as before");
  });
});

// Only TRANSLATE carries over on add/toggle -- rotate/resize are single-item gizmos
// and must not follow onto a new primary just because a Ctrl-click added it to the selection.
test("selectItems('add'/'toggle') resets rotate/resize (not translate) to none", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().selectItems([s1], "only");
    useCellBuilderStore.setState({ gizmoMode: "rotate" } as never);
    st().selectItems([REF_A], "add");
    assert.equal(st().gizmoMode, "none", "rotate does not carry over to the new primary");

    useCellBuilderStore.setState({ gizmoMode: "resize" } as never);
    st().selectItems([s1], "toggle");
    assert.equal(st().gizmoMode, "none", "resize does not carry over either");
  });
});

// "add"/"toggle" with no ids has nothing to add or flip -- a no-op, not a
// re-derive of the (unchanged) primary that also reset the gizmo mode for nothing.
test("selectItems('add'/'toggle', []) is a no-op: the selection and the gizmo mode are untouched", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().selectItems([s1], "only");
    useCellBuilderStore.setState({ gizmoMode: "translate" } as never);

    const before = { selection: st().selection, selectedCellIds: st().selectedCellIds, selectedRefIds: st().selectedRefIds };
    st().selectItems([], "add");
    assert.deepEqual(st().selection, before.selection);
    assert.deepEqual(st().selectedCellIds, before.selectedCellIds);
    assert.deepEqual(st().selectedRefIds, before.selectedRefIds);
    assert.equal(st().gizmoMode, "translate", "not reset");

    st().selectItems([], "toggle");
    assert.equal(st().gizmoMode, "translate", "still not reset");

    // "only" with no ids is still a real clear, unlike "add"/"toggle".
    st().selectItems([], "only");
    assert.equal(st().selection, null);
    assert.equal(st().gizmoMode, "none");
  });
});

// ── selectCells / toggleCellSelection keep their existing, cells-only semantics (regression pin) ──

test("selectCells is a cells-only REPLACE: it drops any stale ref selection too", () => {
  withModel(() => {
    st().toggleRefSelection(REF_A);
    const s1 = cellId("S1");
    st().selectCells([s1]);
    assert.deepEqual(st().selectedCellIds, [s1]);
    assert.deepEqual(st().selection, { kind: "cell", cellId: s1 });
    // selectCells replaces the WHOLE selection, same as setSelection: a stale ref from an older
    // selection writer (or the older-core mixed-selection fallback, which uses selectCells) must
    // not linger and act on a ref the cells-only pick no longer means to include.
    assert.deepEqual(st().selectedRefIds, []);
  });
});

test("toggleCellSelection never touches selectedRefIds", () => {
  withModel(() => {
    st().toggleRefSelection(REF_A);
    const s1 = cellId("S1");
    st().toggleCellSelection(s1);
    assert.deepEqual(st().selectedRefIds, [REF_A]);
    assert.deepEqual(st().selection, { kind: "cell", cellId: s1 });
  });
});

// ── other replace-style selection writers also drop a stale ref ──────────────────────────

// The bug scenario: Ctrl-click a ref (toggleRefSelection: it becomes primary, selectedRefIds =
// [REF_A]), then Ctrl-click a cell (toggleCellSelection: ADDS it, by design leaving selectedRefIds
// untouched -- it is additive). `selection.kind` is "cell" again, but the ref lingers in
// selectedRefIds until a replace-style writer runs, which is exactly what each of these should do.
test("cycleSelectMode clears a stale ref selection", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().toggleRefSelection(REF_A);
    st().toggleCellSelection(s1);
    assert.deepEqual(st().selectedRefIds, [REF_A], "still stale before the replace-style writer runs");
    st().cycleSelectMode(1); // cell -> face
    assert.deepEqual(st().selectedRefIds, []);
    assert.equal(st().selection?.kind, "face");
  });
});

test("cycleSelectionElement clears a stale ref selection", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().toggleRefSelection(REF_A);
    st().toggleCellSelection(s1);
    st().cycleSelectionElement(1); // cycles the box's faces
    assert.deepEqual(st().selectedRefIds, []);
  });
});

test("selectAdjacentCell clears a stale ref selection", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().toggleRefSelection(REF_A);
    st().toggleCellSelection(s1);
    st().selectAdjacentCell(1);
    assert.deepEqual(st().selectedRefIds, []);
  });
});

test("focusEquipment clears a stale ref selection", () => {
  const eqDoc = {
    schema_version: "1.1", grid: {}, blueprint: {},
    spaces: [{ NAME: "S1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }],
    equipments: [{ NAME: "EQ1", X: 0, Y: 0, Z: 0, DX: 1, DY: 1, DZ: 1 }],
  } as unknown as ProceduralDoc;
  st().open("m", "Model", 1, eqDoc);
  const off = registerBuilderRefs({ owner: "sel-test", project: () => [{ key: "a", name: "a", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] }] });
  try {
    st().toggleRefSelection(REF_A);
    assert.deepEqual(st().selectedRefIds, [REF_A]);
    st().focusEquipment("EQ1");
    assert.deepEqual(st().selectedRefIds, []);
    assert.equal(st().selection?.kind, "cell");
  } finally {
    off();
    st().close();
  }
});

test("insertEquipmentAtLocal (placementSlice) clears a stale ref selection", () => {
  withModel(() => {
    const s1 = cellId("S1");
    st().toggleRefSelection(REF_A);
    assert.deepEqual(st().selectedRefIds, [REF_A]);
    st().insertEquipmentAtLocal(s1, [0, 0]);
    assert.deepEqual(st().selectedRefIds, [], "the new equipment's own selection drops the stale ref");
    assert.equal(st().selection?.kind, "cell");
  });
});
