// A ref (a plugin's load/modification/support box) whose structure is left out of the build
// (a group's `include: false`) must dim/hide with it -- not just when the ref's OWN `include` is
// false. Before this, `refsOverlay.ts` read only `ref.include` (utils/cellbuilder/refInclude.ts),
// so a structure row unticked in the Model panel left its loads/supports drawn at full strength
// in the 3D view. This covers the scene-level effect; `refInclude.test.ts` covers
// `refIncludeState` itself, pure.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { rebuildRefs } = await import("@/components/viewer/cellbuilder/refsOverlay");
type Vec3 = [number, number, number];

const st = () => useCellBuilderStore.getState();
const REF = "structure-include-test:r";
const START = { origin: [0, 0, 0] as Vec3, size: [2, 1, 1] as Vec3 };

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  groups: [{ name: "A", blueprint: "steel_stru" }],
  spaces: [{ NAME: "S1", STRUCTURE_NAME: "A", X: 0, Y: 0, Z: 0, DX: 1, DY: 1, DZ: 1 }],
  equipments: [],
  boxes: [{ ...START }],
} as unknown as ProceduralDoc;

function makeCtx() {
  return {
    refsGroup: new THREE.Group(),
    refMeshById: new Map(),
    cellsGroup: new THREE.Group(),
  } as unknown as Parameters<typeof rebuildRefs>[0];
}

/** Opens `doc`, registers one ref naming structure "A", and runs `run(ctx, setGroupIncluded)`. */
function withRefOnStructureA(run: (ctx: ReturnType<typeof makeCtx>) => void): void {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "structure-include-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b) => ({
        key: "r",
        name: "r",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
        structure: "A",
        edit: { move: true, resize: true },
      })),
  });
  const ctx = makeCtx();
  try {
    run(ctx);
  } finally {
    off();
    st().close();
  }
}

test("a ref on an INCLUDED structure draws undimmed", () => {
  withRefOnStructureA((ctx) => {
    rebuildRefs(ctx);
    const mesh = ctx.refMeshById.get(REF);
    assert.ok(mesh, "the ref's mesh was built");
    assert.equal((mesh!.userData.__includeLook as { dimmed: boolean }).dimmed, false);
  });
});

test("a ref on an EXCLUDED structure draws dimmed, like a cell of that structure", () => {
  withRefOnStructureA((ctx) => {
    // Exclude structure "A" (the Model panel's structure-row checkbox).
    st().setGroupIncluded("A", false);
    rebuildRefs(ctx);
    const mesh = ctx.refMeshById.get(REF);
    assert.ok(mesh, "the ref's mesh is still built (own include is still true)");
    assert.equal((mesh!.userData.__includeLook as { dimmed: boolean }).dimmed, true);
  });
});

test("re-including the structure undims the ref again on the next rebuild", () => {
  withRefOnStructureA((ctx) => {
    st().setGroupIncluded("A", false);
    rebuildRefs(ctx);
    assert.equal((ctx.refMeshById.get(REF)!.userData.__includeLook as { dimmed: boolean }).dimmed, true);

    st().setGroupIncluded("A", true);
    rebuildRefs(ctx);
    assert.equal((ctx.refMeshById.get(REF)!.userData.__includeLook as { dimmed: boolean }).dimmed, false);
  });
});
