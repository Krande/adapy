// The 3D joint markers are a view of the RESULT, derived the same way the panel's rows are: one
// marker per joint, coloured by the group the joint belongs to. These tests pin the two facts a
// user can see: the swatch on a row and the spheres it points at are the same colour, and the
// markers a filtered list leaves on screen are the markers the 3D view draws.
//
// Browser globals are stubbed before the dynamic import for the reason given in
// `parseClashResult.test.ts` -- `@/state/clashCheckStore` reaches `sessionStorage` at load.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { parseClashResult, groupColor, jointMarkers, hueColor } = await import("@/state/clashCheckStore");

function fixtureDoc(): Record<string, unknown> {
  const joint = (id: string, typeKey: string, x: number) => ({
    id,
    centre: [x, 0, 0],
    members: [{ name: `bm${id}`, kind: "BEAM", section: "I", member_type: "GIRDER" }],
    type_key: typeKey,
    type_label: typeKey,
    applicable: [],
  });
  return {
    schema: "ada.clash/result@1",
    source_key: "models/plant-a.ifc",
    options: {},
    counts: { members: 6, joints: 3 },
    joints: [joint("j1", "a", 0), joint("j2", "a", 1), joint("j3", "b", 2)],
    groups: [
      { type_key: "a", type_label: "A", count: 2, joint_ids: ["j1", "j2"], applicable: [] },
      { type_key: "b", type_label: "B", count: 1, joint_ids: ["j3"], applicable: [] },
    ],
    provenance: {},
    warnings: [],
  };
}

test("one marker per joint, carrying its group's colour", () => {
  const result = parseClashResult(fixtureDoc());
  const markers = jointMarkers(result);
  assert.equal(markers.length, 3);
  assert.deepEqual(
    markers.map((m) => m.id),
    ["j1", "j2", "j3"],
  );
  // Same group, same colour -- and it is the colour the row's swatch renders.
  assert.equal(markers[0].color, markers[1].color);
  assert.equal(markers[0].color, groupColor(result, "a"));
  assert.notEqual(markers[2].color, markers[0].color);
  assert.equal(markers[2].color, groupColor(result, "b"));
});

test("colours are stable hex strings THREE and CSS both read", () => {
  for (let i = 0; i < 12; i++) assert.match(hueColor(i), /^#[0-9a-f]{6}$/);
  assert.equal(hueColor(3), hueColor(3));
});

test("an unknown type key still gets a colour rather than throwing", () => {
  const result = parseClashResult(fixtureDoc());
  assert.match(groupColor(result, "not-a-group"), /^#[0-9a-f]{6}$/);
});

test("filtered-away groups draw no markers", () => {
  const result = parseClashResult(fixtureDoc());
  const markers = jointMarkers(result, { visibleTypeKeys: new Set(["b"]) });
  assert.deepEqual(
    markers.map((m) => m.id),
    ["j3"],
  );
});

test("expanding a group emphasises its markers without hiding the rest", () => {
  const result = parseClashResult(fixtureDoc());
  const markers = jointMarkers(result, { highlight: "b" });
  // Nothing is dropped: the whole frame stays visible, which is the point of drawing them at all.
  assert.equal(markers.length, 3);
  const b = markers.find((m) => m.id === "j3");
  const a = markers.find((m) => m.id === "j1");
  assert.equal(b?.emphasised, true);
  assert.equal(a?.emphasised, false);
  assert.ok((b?.scale ?? 0) > (a?.scale ?? 0));
});

test("no highlight means no emphasis and one uniform scale", () => {
  const result = parseClashResult(fixtureDoc());
  const markers = jointMarkers(result);
  assert.ok(markers.every((m) => m.emphasised && m.scale === 1));
});

// --- which model a selection resolves against -------------------------------------------------

const { checkedSourceName } = await import("@/state/clashCheckStore");

test("selection resolves against the checked model, not the overlay a detail run added", () => {
  // The produced joints load as a SECOND source and become the loaded one. Their GLB carries none
  // of the original member names, so resolving against it selects nothing -- which is what made
  // clicking a row or a marker go quiet until the overlay was removed by hand.
  assert.equal(checkedSourceName("steel-demo.ifc", "model.glb"), "steel-demo.ifc");
});

test("before a check has run, the loaded model is the only answer there is", () => {
  assert.equal(checkedSourceName(null, "steel-demo.ifc"), "steel-demo.ifc");
  assert.equal(checkedSourceName(null, null), null);
});
