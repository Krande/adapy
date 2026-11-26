// Section planes must cut the builder's own objects: spaces, equipment boxes and the
// plugins' refs (loads, modifications, supports), not just the loaded model. clipWithModel tags a
// subtree so a LATER plane add/toggle reaches it (SectionPlanesController's own scene.traverse), and
// applies the CURRENTLY active planes right away — needed because every rebuild re-creates these
// materials from scratch (meshRebuild.ts, refsOverlay.ts), so a rebuild after a plane already exists
// must not draw a moment of unclipped geometry.

import assert from "node:assert/strict";
import { test } from "node:test";
import * as THREE from "three";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { rebuild } = await import("@/components/viewer/cellbuilder/meshRebuild");
const { rebuildRefs } = await import("@/components/viewer/cellbuilder/refsOverlay");
const { setSectionClippingPlanes } = await import("@/utils/scene/section_clipping");

const st = () => useCellBuilderStore.getState();
type Vec3 = [number, number, number];

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "A1", X: 0, Y: 0, Z: 0, DX: 2, DY: 2, DZ: 2 }],
  equipments: [],
  openings: [],
  boxes: [{ origin: [0, 0, 0] as Vec3, size: [1, 1, 1] as Vec3 }],
} as unknown as ProceduralDoc;

/** Enough of the builder scene for `rebuild` and `rebuildRefs`: no canvas, no CAD (equipmentCad is
 *  unset, so the CAD-fit lookup bails out before touching anything this fixture does not have). */
function makeCtx() {
  const container = new THREE.Group();
  const group = () => {
    const g = new THREE.Group();
    container.add(g);
    return g;
  };
  return {
    container,
    cellsGroup: group(),
    refsGroup: group(),
    companionsGroup: group(),
    cadPreviewGroup: group(),
    meshById: new Map(),
    refMeshById: new Map(),
    cadPreviewShown: new Set<string>(),
    cadPreviewCache: new Map(),
    hovered: null,
    hoveredEdge: null,
    ghost: { visible: false },
    ghostBox: null,
    selectedEdgeLine: { visible: false },
    hoverEdgeLine: { visible: false },
    faceOverlay: { visible: false },
    host: { rect: () => ({ left: 0, top: 0, width: 100, height: 100 }) },
  } as unknown as Parameters<typeof rebuild>[0];
}

/** Every material a group's meshes and lines carry, flattened. */
function materialsOf(root: THREE.Object3D): THREE.Material[] {
  const out: THREE.Material[] = [];
  root.traverse((o) => {
    const m = (o as unknown as { material?: THREE.Material | THREE.Material[] }).material;
    if (!m) return;
    out.push(...(Array.isArray(m) ? m : [m]));
  });
  return out;
}

test("rebuild tags and clips every cell's materials under the active section planes", () => {
  const planes = [new THREE.Plane(new THREE.Vector3(0, 0, 1), 0)];
  setSectionClippingPlanes(planes);
  st().open("m", "Model", 1, doc);
  try {
    const ctx = makeCtx();
    rebuild(ctx);
    assert.ok(ctx.meshById.size > 0, "the space got a mesh");
    const mats = materialsOf(ctx.cellsGroup);
    assert.ok(mats.length > 0);
    for (const m of mats) {
      assert.equal(m.clippingPlanes, planes, "every material is clipped by the active planes");
    }
    // userData is tagged on every descendant, not only ones with a material (SectionPlanesController's
    // own traverse checks the tag first, for any object type, so a later plane edit still reaches it).
    let tagged = 0;
    let total = 0;
    ctx.cellsGroup.traverse((o) => {
      total++;
      if (o.userData.__clipWithModel) tagged++;
    });
    assert.equal(tagged, total, "every descendant is tagged");
  } finally {
    st().close();
    setSectionClippingPlanes(null);
  }
});

test("rebuildRefs tags and clips every ref's materials under the active section planes", () => {
  const planes = [new THREE.Plane(new THREE.Vector3(1, 0, 0), -5)];
  setSectionClippingPlanes(planes);
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "section-test",
    project: (extras) =>
      ((extras.boxes as { origin: Vec3; size: Vec3 }[] | undefined) ?? []).map((b) => ({
        key: "r",
        name: "r",
        kindLabel: "Box",
        origin: b.origin,
        size: b.size,
      })),
  });
  try {
    const ctx = makeCtx();
    rebuildRefs(ctx);
    assert.ok(ctx.refMeshById.size > 0, "the ref got a mesh");
    const mats = materialsOf(ctx.refsGroup);
    assert.ok(mats.length > 0);
    for (const m of mats) assert.equal(m.clippingPlanes, planes);
  } finally {
    off();
    st().close();
    setSectionClippingPlanes(null);
  }
});

test("without an active plane, rebuild still tags (so a plane added afterwards reaches it), but clips nothing", () => {
  setSectionClippingPlanes(null);
  st().open("m", "Model", 1, doc);
  try {
    const ctx = makeCtx();
    rebuild(ctx);
    const mats = materialsOf(ctx.cellsGroup);
    assert.ok(mats.length > 0);
    for (const m of mats) assert.equal(m.clippingPlanes, null);
    let tagged = 0;
    ctx.cellsGroup.traverse((o) => {
      if (o.userData.__clipWithModel) tagged++;
    });
    assert.ok(tagged > 0, "still tagged for a later plane");
  } finally {
    st().close();
  }
});
