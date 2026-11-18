// The 3D menu's Add door / Add window / Add hatch on a space's side made the new opening's box snapped
// to the grid in every axis, its 0.3 m through-thickness too: on a coarse grid (1.3 m) that rounded to
// nothing, so the opening lay in the face plane and the engine could not cut it (OCCT
// Standard_DomainError). Through the face it now keeps its thickness, straddling the face plane.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "deck", X: 0, Y: 0, Z: 0, DX: 5.2, DY: 7.8, DZ: 3.9 }],
  equipments: [],
  openings: [],
} as unknown as ProceduralDoc;

const st = () => useCellBuilderStore.getState();
const added = () => Object.values(st().cells).find((c) => c.kind === "opening")!;
const space = () => Object.values(st().cells).find((c) => c.kind === "cell")!;
const written = () => (st().toDoc() as unknown as { openings: Record<string, number | string | boolean>[] }).openings[0];

for (const [label, face, subtype, axis, plane] of [
  ["a door on the -Y side", 3, "door", 1, 0],
  ["a window on the +X side", 0, "window", 0, 5.2],
  ["a hatch in the roof", 4, "opening", 2, 3.9],
] as const) {
  test(`${label} has a through-thickness on a coarse grid, about the face plane`, () => {
    st().open("m", "Model", 1, doc);
    st().setGridStep(1.3);
    st().insertOpeningOnFace(space().id, face, subtype);
    const o = added();
    assert.ok(Math.abs(o.size[axis] - 0.3) < 1e-9, `through the face: ${o.size[axis]}`);
    assert.ok(Math.abs(o.origin[axis] + o.size[axis] / 2 - plane) < 1e-9, "centred on the face plane");
    for (const a of [0, 1, 2]) assert.ok(o.size[a] > 0, `size ${a} is ${o.size[a]}`);
    const w = written();
    assert.ok(Number(w.DX) > 0 && Number(w.DY) > 0 && Number(w.DZ) > 0, JSON.stringify(w));
  });
}
