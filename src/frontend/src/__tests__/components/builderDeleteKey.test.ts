// The Delete / Backspace key: a mixed cell+ref selection used to delete only the
// cells (or, with a lone ref primary, only that one ref), silently leaving the rest of a
// multi-selection untouched. It now removes both sets in one undo step, refs in reverse selection
// order -- the best a position-agnostic caller can do for a provider whose ref ids are keyed by
// row position (a provider that keys its refs by name instead, so a delete never has to renumber
// the rest, is the real fix for that; core cannot know a provider's key scheme).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { onKeyDown } = await import("@/components/viewer/cellbuilder/keyboard");

const st = () => useCellBuilderStore.getState();
type Vec3 = [number, number, number];

const BOXES = [
  { origin: [0, 0, 0] as Vec3, size: [1, 1, 1] as Vec3 },
  { origin: [1, 1, 1] as Vec3, size: [1, 1, 1] as Vec3 },
  { origin: [2, 2, 2] as Vec3, size: [1, 1, 1] as Vec3 },
];

const makeDoc = (boxes: typeof BOXES) =>
  ({
    schema_version: "1.1",
    grid: {},
    blueprint: {},
    spaces: [{ NAME: "S1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }, { NAME: "S2", X: 5, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }],
    equipments: [],
    boxes,
  }) as unknown as ProceduralDoc;

const doc = makeDoc(BOXES);

const keyCtx = { numEntry: null, placeEntry: null, equipEntry: null, openEntry: null, modalMove: null, loftActive: null } as never;
const del = () => ({ key: "Delete", code: "Delete", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, target: null, preventDefault() {}, stopPropagation() {} }) as unknown as KeyboardEvent;
const cellId = (name: string) => Object.values(st().cells).find((c) => c.name === name)!.id;

/** Position-keyed refs ("#0", "#1", ...), as a plugin's loads/supports can be: `remove`
 *  splices the doc array by index. */
function positionalRefsProvider() {
  return registerBuilderRefs({
    owner: "del-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b, i) => ({
        key: `#${i}`,
        name: `box ${i}`,
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        edit: { remove: true },
      })),
    remove: (ref) => {
      const idx = Number(ref.key.slice(1));
      const boxes = (st().docExtras.boxes as { origin: Vec3; size: Vec3 }[]) ?? [];
      st().setDocExtra("boxes", boxes.filter((_, i) => i !== idx));
    },
  });
}

test("a mixed cell+ref selection deletes both, in one undo step", () => {
  // A single box: no positional-key reuse to confuse "is del-test:#0 still there" (the next test
  // covers several refs and the reuse that reverse ordering guards against).
  st().open("m", "Model", 1, makeDoc([BOXES[0]]));
  const off = positionalRefsProvider();
  try {
    const s1 = cellId("S1");
    st().selectItems([s1, "del-test:#0"], "only");
    assert.deepEqual(st().selectedCellIds, [s1]);
    assert.deepEqual(st().selectedRefIds, ["del-test:#0"]);
    const past = st().past.length;
    onKeyDown(keyCtx, del());
    assert.equal(st().cells[s1], undefined, "the cell is gone");
    assert.equal((st().docExtras.boxes as unknown[]).length, 0, "the ref is gone too");
    assert.equal(st().past.length, past + 1, "one undo step for the whole delete");
    st().undo();
    assert.ok(st().cells[s1], "the cell comes back");
    assert.equal((st().docExtras.boxes as unknown[]).length, 1, "and so does the ref, in the same undo");
  } finally {
    off();
    st().close();
  }
});

test("several selected refs are removed in REVERSE selection order, so a position-keyed provider's shifting ids stay valid", () => {
  st().open("m", "Model", 1, doc);
  const removedAt: number[] = [];
  const off = registerBuilderRefs({
    owner: "del-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b, i) => ({
        key: `#${i}`,
        name: `box ${i}`,
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        edit: { remove: true },
      })),
    remove: (ref) => {
      const idx = Number(ref.key.slice(1));
      removedAt.push(idx);
      const boxes = (st().docExtras.boxes as { origin: Vec3; size: Vec3 }[]) ?? [];
      st().setDocExtra("boxes", boxes.filter((_, i) => i !== idx));
    },
  });
  try {
    // Select box 0 and box 2 (the middle one, box 1, is left alone): selectItems lists them
    // "0 then 2", the order a Ctrl-click or a box select would give.
    st().selectItems(["del-test:#0", "del-test:#2"], "only");
    assert.deepEqual(st().selectedRefIds, ["del-test:#0", "del-test:#2"]);
    onKeyDown(keyCtx, del());
    // Reverse order removes #2 while #0's id is still valid, then #0: box 1 (the untouched one)
    // is the only one left. Forward order would instead remove box 0, re-project (box 1 becomes
    // "#0", box 2 becomes "#1"), and then fail to find a now-nonexistent "#2" -- leaving box 2
    // behind and nothing else removed.
    assert.deepEqual(removedAt, [2, 0], "removed highest index first");
    const boxes = st().docExtras.boxes as { origin: Vec3 }[];
    assert.equal(boxes.length, 1);
    assert.deepEqual(boxes[0].origin, [1, 1, 1], "box 1 (the untouched one) survives");
  } finally {
    off();
    st().close();
  }
});

test("a cell and a non-removable ref together: the cell is deleted, the ref is left (its plugin refuses)", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "del-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).slice(0, 1).map((b) => ({
        key: "#0",
        name: "box 0",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        // No `remove` callback at all: edit.remove is forced false regardless of the spec (toRef).
      })),
  });
  try {
    const s1 = cellId("S1");
    st().selectItems([s1, "del-test:#0"], "only");
    onKeyDown(keyCtx, del());
    assert.equal(st().cells[s1], undefined, "the cell is still deleted");
    assert.ok(st().refs["del-test:#0"], "the ref survives: its plugin never offered remove");
  } finally {
    off();
    st().close();
  }
});

test("a lone, non-removable ref selection leaves the key unclaimed (today's behaviour)", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "del-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).slice(0, 1).map((b) => ({
        key: "#0",
        name: "box 0",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
      })),
  });
  try {
    st().setSelection({ kind: "ref", refId: "del-test:#0" });
    let prevented = false;
    const ev = { key: "Delete", code: "Delete", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, target: null, preventDefault: () => void (prevented = true), stopPropagation() {} } as unknown as KeyboardEvent;
    onKeyDown(keyCtx, ev);
    assert.equal(prevented, false, "nothing to delete here: the key is left for whatever else handles it");
    assert.ok(st().refs["del-test:#0"], "untouched");
  } finally {
    off();
    st().close();
  }
});
