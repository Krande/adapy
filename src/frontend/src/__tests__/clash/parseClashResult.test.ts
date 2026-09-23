// `parseClashResult` is the one place this viewer reads a provider-... no -- a CORE-authored
// `ada.clash/result@1` document and decides to trust it. Pins:
//   - an unknown schema is REFUSED, never partially read (mirrors `parse_clash_result` in
//     `ada/clash/result.py`, and `parseBuildSummary`/`parseHierarchy` elsewhere in this viewer).
//   - a non-object document is refused the same way.
//   - `groups[].count` sums to `joints.length` -- the property the panel's rows depend on
//     (`ada/clash/result.py`'s `group_joints` docstring: "a joint that fell out of every group
//     would be invisible in the only view that lists them").
//   - `jointsById` is populated from `joints[]` so `jointsForGroup` never rescans the array.
//
// `@/state/clashCheckStore` transitively imports `@/services/jobTracking` ->
// `@/services/viewerApi` -> `@/services/auth/oidc`, which reads `sessionStorage` at module load
// (the refresh-token cache) -- so the browser globals are stubbed BEFORE the dynamic import, the
// same shape `__tests__/services/jobTracking.test.ts` uses for the same reason.

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

const { parseClashResult, ClashResultError } = await import("@/state/clashCheckStore");

function fixtureDoc(): Record<string, unknown> {
  return {
    schema: "ada.clash/result@1",
    source_key: "models/plant-a.ifc",
    source_sha256: "abc123",
    options: { out_of_plane_tol: 0.1, point_tol: 1e-5, root: null, include_plate_joints: true },
    counts: { members: 4, beams: 4, plates: 0, joints: 2, joints_with_a_generator: 1 },
    joints: [
      {
        id: "j1",
        centre: [0, 0, 0],
        members: [
          { name: "bm1", kind: "BEAM", section: "I", member_type: "GIRDER" },
          { name: "bm2", kind: "BEAM", section: "I", member_type: "COLUMN" },
        ],
        type_key: "2|BEAM:I:GIRDER+BEAM:I:COLUMN|perpendicular",
        type_label: "2 × BEAM · I · Girder→Column · perpendicular",
        applicable: [{ spec: "builtin.girder_gusset", priority: 10 }],
      },
      {
        id: "j2",
        centre: [1, 0, 0],
        members: [
          { name: "bm3", kind: "BEAM", section: "BOX", member_type: "BRACE" },
          { name: "bm4", kind: "BEAM", section: "BOX", member_type: "BRACE" },
        ],
        type_key: "2|BEAM:BOX:BRACE+BEAM:BOX:BRACE|parallel",
        type_label: "2 × BEAM · BOX · Brace · parallel",
        applicable: [{ spec: "external.box_spec", capability: "box-pool", priority: 5 }],
      },
    ],
    groups: [
      {
        type_key: "2|BEAM:I:GIRDER+BEAM:I:COLUMN|perpendicular",
        type_label: "2 × BEAM · I · Girder→Column · perpendicular",
        count: 1,
        joint_ids: ["j1"],
        applicable: [{ spec: "builtin.girder_gusset", priority: 10 }],
      },
      {
        type_key: "2|BEAM:BOX:BRACE+BEAM:BOX:BRACE|parallel",
        type_label: "2 × BEAM · BOX · Brace · parallel",
        count: 1,
        joint_ids: ["j2"],
        applicable: [{ spec: "external.box_spec", capability: "box-pool", priority: 5 }],
      },
    ],
    provenance: { provider: "core", adapy_version: "0.99.0" },
    warnings: [],
  };
}

test("parses a well-formed result and fills jointsById", () => {
  const result = parseClashResult(fixtureDoc());
  assert.equal(result.schema, "ada.clash/result@1");
  assert.equal(result.sourceKey, "models/plant-a.ifc");
  assert.equal(result.joints.length, 2);
  assert.equal(result.groups.length, 2);
  assert.equal(result.jointsById.size, 2);
  assert.equal(result.jointsById.get("j1")?.typeLabel, "2 × BEAM · I · Girder→Column · perpendicular");
  // capability absent on the wire -> null in the model, not undefined -- every reader tests one
  // falsy shape.
  assert.equal(result.jointsById.get("j1")?.applicable[0].capability, null);
  assert.equal(result.jointsById.get("j2")?.applicable[0].capability, "box-pool");
});

test("refuses an unknown schema rather than reading it partially", () => {
  const doc = { ...fixtureDoc(), schema: "ada.clash/result@0" };
  assert.throws(() => parseClashResult(doc), ClashResultError);
});

test("refuses a non-object document", () => {
  assert.throws(() => parseClashResult(null), ClashResultError);
  assert.throws(() => parseClashResult([1, 2, 3]), ClashResultError);
  assert.throws(() => parseClashResult("not json"), ClashResultError);
});

test("groups[].count sums to joints.length", () => {
  const result = parseClashResult(fixtureDoc());
  const summed = result.groups.reduce((s, g) => s + g.count, 0);
  assert.equal(summed, result.joints.length);
});
