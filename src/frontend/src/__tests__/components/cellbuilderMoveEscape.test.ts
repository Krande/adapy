// The builder's keyboard: Escape during an axis-locked move (G, then X). Pressed in the gizmo
// HUD's delta field - which takes the focus as the axis locks - it must cancel the move as it
// does on the canvas: the cell back where it began, the move measure gone, the lock dropped.
// It left the field only, with the cell where the pointer had taken it and the measure drawn.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const THREE = await import("three");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { onKeyDown } = await import("@/components/viewer/cellbuilder/keyboard");
const { GIZMO_DELTA_FIELD_ATTR } = await import("@/components/viewer/cellbuilder/sceneConstants");
const { syncMoveMeasure } = await import("@/components/viewer/cellbuilder/cellDimensions");

const start = { origin: [0, 0, 0] as [number, number, number], size: [2, 2, 2] as [number, number, number] };
const moved = { origin: [3, 0, 0] as [number, number, number], size: start.size };

/** The scene while a modal move along X has carried the cell 3 m: what Escape reads and resets. */
function liveMove() {
  const moveMeasureGroup = new THREE.Group();
  // The element the view's pointer arrives on: its cursor.
  const input = { style: { cursor: "move" } };
  const ctx = {
    modalMove: { cellId: "c1", axis: 0 as const, lineDir: new THREE.Vector3(1, 0, 0), lineOrigin: new THREE.Vector3(), startT: 0, startBox: start },
    moveMeasure: { cellId: "c1", start },
    moveMeasureGroup,
    translateEquip: [] as string[],
    gizmo: { enabled: false },
    gizmoHelper: { visible: false },
    guideLine: { visible: true },
    snapMarker: { visible: true },
    input,
    host: { input: () => input, camera: () => new THREE.PerspectiveCamera() },
  };
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: { c1: { id: "c1", name: "A", kind: "cell", ...moved } },
    selection: { kind: "cell", cellId: "c1" },
    selectedCellIds: ["c1"],
    gizmoMode: "translate",
    gizmoAxisLock: 0,
  } as never);
  syncMoveMeasure(ctx as never, start, moved);
  assert.ok(moveMeasureGroup.children.length > 0 && moveMeasureGroup.visible, "the measure is drawn");
  return ctx;
}

const escape = (target: { tagName: string; hasAttribute(n: string): boolean } | null) => {
  let prevented = false;
  const ev = { key: "Escape", target, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, preventDefault: () => void (prevented = true), stopPropagation: () => {} };
  return { ev: ev as unknown as KeyboardEvent, prevented: () => prevented };
};

test("Escape in the HUD's Move field cancels the move: cell back, measure gone, lock dropped", () => {
  const ctx = liveMove();
  const { ev, prevented } = escape({ tagName: "INPUT", hasAttribute: (n) => n === GIZMO_DELTA_FIELD_ATTR });
  onKeyDown(ctx as never, ev);
  const st = useCellBuilderStore.getState();
  assert.deepEqual(st.cells.c1.origin, [0, 0, 0], "back where the move began");
  assert.equal(ctx.modalMove, null);
  assert.equal(ctx.moveMeasure, null);
  assert.equal(ctx.moveMeasureGroup.children.length, 0, "the measure is gone");
  assert.equal(ctx.moveMeasureGroup.visible, false);
  assert.equal(st.gizmoAxisLock, null, "the lock is dropped");
  assert.equal(st.gizmoMode, "translate", "the gizmo itself stays (a second Escape closes it)");
  assert.equal(ctx.guideLine.visible, false);
  assert.equal(ctx.input.style.cursor, "");
  assert.equal(prevented(), true);
});

test("Escape on the canvas cancels it the same way", () => {
  const ctx = liveMove();
  onKeyDown(ctx as never, escape(null).ev);
  assert.deepEqual(useCellBuilderStore.getState().cells.c1.origin, [0, 0, 0]);
  assert.equal(ctx.moveMeasure, null);
  assert.equal(useCellBuilderStore.getState().gizmoAxisLock, null);
});

test("Escape in another field only leaves that field: the move goes on", () => {
  const ctx = liveMove();
  const { ev, prevented } = escape({ tagName: "INPUT", hasAttribute: () => false });
  onKeyDown(ctx as never, ev);
  assert.deepEqual(useCellBuilderStore.getState().cells.c1.origin, [3, 0, 0], "still where the pointer took it");
  assert.ok(ctx.modalMove, "the move is live");
  assert.ok(ctx.moveMeasureGroup.children.length > 0, "and measured");
  assert.equal(useCellBuilderStore.getState().gizmoAxisLock, 0);
  assert.equal(prevented(), false);
});
