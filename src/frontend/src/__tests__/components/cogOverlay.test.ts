// A COG marker for the SELECTED equipment only (no view toggle -- deselecting it is the
// only way to hide it). COGx/COGy are offsets from the equipment's footprint CENTRE, COGz from its
// BASE (src/ada/topology/entities.py get_cog); missing ones default as the doc writer's own
// (COGx=0, COGy=0, COGz=size[2]/2). See cogOverlay.ts.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { rebuildCog } = await import("@/components/viewer/cellbuilder/cogOverlay");
import type { CellBuilderScene } from "@/components/viewer/cellbuilder/sceneContext";

const st = () => useCellBuilderStore.getState();

function makeCtx(): CellBuilderScene {
  return { cogGroup: new THREE.Group() } as unknown as CellBuilderScene;
}

function model(cellsPatch: Record<string, unknown>) {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: cellsPatch,
    groups: [],
    loftMembers: [],
    selection: null,
    selectedCellIds: [],
    hiddenCellIds: [],
    cellsVisible: true,
  } as never);
}

/** All marker groups across every structure frame in ctx.cogGroup. */
function markers(ctx: CellBuilderScene): THREE.Object3D[] {
  return ctx.cogGroup.children.flatMap((frame) => frame.children);
}

const EQ = {
  id: "e1",
  name: "Pump1",
  kind: "equipment" as const,
  origin: [10, 20, 1] as [number, number, number],
  size: [2, 1, 1] as [number, number, number],
  params: {} as Record<string, unknown>,
};

test("nothing selected: no marker", () => {
  model({ e1: EQ });
  const ctx = makeCtx();
  rebuildCog(ctx);
  assert.equal(markers(ctx).length, 0);
});

test("a selected equipment gets a marker at its COG, defaults applied for missing params", () => {
  model({ e1: { ...EQ, params: { COGx: 0.5 } } });
  useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "e1" } } as never);
  const ctx = makeCtx();
  rebuildCog(ctx);
  const marks = markers(ctx);
  assert.equal(marks.length, 1);
  // pivot = origin + [size0/2, size1/2, 0] = (11, 20.5, 1); COG = (0.5, 0 default, size[2]/2=0.5 default).
  const m = marks[0];
  assert.ok(Math.abs(m.position.x - 11.5) < 1e-9, String(m.position.x));
  assert.ok(Math.abs(m.position.y - 20.5) < 1e-9, String(m.position.y));
  assert.ok(Math.abs(m.position.z - 1.5) < 1e-9, String(m.position.z));
  assert.equal(m.userData.__cellId, "e1");
});

test("deselecting removes the marker", () => {
  model({ e1: EQ });
  useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "e1" } } as never);
  const ctx = makeCtx();
  rebuildCog(ctx);
  assert.equal(markers(ctx).length, 1);
  useCellBuilderStore.setState({ selection: null } as never);
  rebuildCog(ctx);
  assert.equal(markers(ctx).length, 0);
});

test("a multi-selection (selectedCellIds) gets one marker each; a non-equipment selected gets none", () => {
  model({
    e1: EQ,
    e2: { ...EQ, id: "e2", name: "Pump2", origin: [0, 0, 0] as [number, number, number] },
    s1: { id: "s1", name: "Room", kind: "cell" as const, origin: [0, 0, 0] as [number, number, number], size: [4, 4, 3] as [number, number, number], params: {} },
  });
  useCellBuilderStore.setState({ selectedCellIds: ["e1", "e2", "s1"] } as never);
  const ctx = makeCtx();
  rebuildCog(ctx);
  assert.equal(markers(ctx).length, 2, "only the two equipment, not the space");
});

test("a rotated equipment's COG offset rotates about the box's base-footprint pivot", () => {
  // A 90-degree yaw (ROT_Z): local +X (towards COGx) becomes world +Y.
  model({ e1: { ...EQ, origin: [0, 0, 0], rotation: [0, 0, 90], params: { COGx: 1, COGy: 0, COGz: 0 } } });
  useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "e1" } } as never);
  const ctx = makeCtx();
  rebuildCog(ctx);
  const m = markers(ctx)[0];
  // pivot = (1, 0.5, 0) (size [2,1,1]); offset (1,0,0) rotated 90 deg about Z -> (0,1,0).
  assert.ok(Math.abs(m.position.x - 1) < 1e-9, String(m.position.x));
  assert.ok(Math.abs(m.position.y - 1.5) < 1e-9, String(m.position.y));
  assert.ok(Math.abs(m.position.z - 0) < 1e-9, String(m.position.z));
});

test("a rotated CAD-fitted equipment's COG offset still rotates with the body", () => {
  // Same 90-degree yaw as the plain rotated-equipment test above, but this equipment has a
  // loaded CAD mesh. The CAD's own compiled geometry is rotated (so its *preview box* stays
  // axis-aligned -- the rotation is already baked into the mesh's bounds), but the real body,
  // and therefore its COG, is still rotated about the footprint pivot.
  model({ e1: { ...EQ, origin: [0, 0, 0], rotation: [0, 0, 90], params: { COGx: 1, COGy: 0, COGz: 0 } } });
  useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "e1" }, equipmentCad: true } as never);
  const ctx = {
    cogGroup: new THREE.Group(),
    container: new THREE.Object3D(),
    host: {
      loadedGeometry: (name: string) =>
        name === "Pump1" ? { box: new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1)), points: () => [] } : null,
    },
  } as unknown as CellBuilderScene;
  try {
    rebuildCog(ctx);
    const m = markers(ctx)[0];
    // pivot = (1, 0.5, 0) (size [2,1,1]); offset (1,0,0) rotated 90 deg about Z -> (0,1,0).
    assert.ok(Math.abs(m.position.x - 1) < 1e-9, String(m.position.x));
    assert.ok(Math.abs(m.position.y - 1.5) < 1e-9, String(m.position.y));
    assert.ok(Math.abs(m.position.z - 0) < 1e-9, String(m.position.z));
  } finally {
    useCellBuilderStore.setState({ equipmentCad: false } as never);
  }
});

test("an individually hidden equipment, or the builder hidden altogether, gets no marker", () => {
  model({ e1: EQ });
  useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "e1" }, hiddenCellIds: ["e1"] } as never);
  let ctx = makeCtx();
  rebuildCog(ctx);
  assert.equal(markers(ctx).length, 0, "hidden individually");

  useCellBuilderStore.setState({ hiddenCellIds: [], cellsVisible: false } as never);
  ctx = makeCtx();
  rebuildCog(ctx);
  assert.equal(markers(ctx).length, 0, "the builder itself hidden");
});
