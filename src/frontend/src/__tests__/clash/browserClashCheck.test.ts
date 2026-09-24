// The browser clash route: adacpp's compiled beam-to-beam pass through embind, no Python runtime.
//
// The RULES are not tested here -- they are C++, and adapy holds them to the Python pass in
// `tests/core/clash/test_native_beam_pass_parity.py`. What can break HERE is the assembly: that
// the compiled pass's joints become the `ada.clash/result@1` document the panel already reads,
// and above all that the joint IDS match the ones a server-side detail hand-off re-derives.
//
// Browser globals are stubbed before the dynamic import, as in `parseClashResult.test.ts`.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null =>
    memory.has(k) ? (memory.get(k) as string) : null,
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as {
  sessionStorage: unknown;
  localStorage: unknown;
};
globals.sessionStorage = storage;
globals.localStorage = storage;

function nativeDoc() {
  return {
    schema: "adacpp.clash_joints/1",
    beams: 3,
    joints: [
      {
        origin: "beam-beam",
        centre: [5, 0, 0],
        angle_deg: 90,
        type_key: "3|BEAM:I:COLUMN+BEAM:I:GIRDER+BEAM:I:GIRDER|perpendicular",
        members: [
          {
            name: "g0",
            guid: "a",
            kind: "BEAM",
            section: "I",
            member_type: "Girder",
          },
          {
            name: "g1",
            guid: "b",
            kind: "BEAM",
            section: "I",
            member_type: "Girder",
          },
          {
            name: "c0",
            guid: "c",
            kind: "BEAM",
            section: "I",
            member_type: "Column",
          },
        ],
      },
    ],
  };
}

const calls: Array<{ outOfPlaneTol: number; pointTol: number }> = [];
function fakeDeps() {
  calls.length = 0;
  return {
    clashJoints: async (
      _bytes: ArrayBuffer,
      opts: { outOfPlaneTol: number; pointTol: number },
    ) => {
      calls.push(opts);
      return { json: JSON.stringify(nativeDoc()), joints: 1, ms: 3 };
    },
  };
}

const { browserClashCheckSupports, runBrowserClashCheck } = await import(
  "@/services/clash/browserClashCheck"
);

test("the route is offered for IFC and withheld for everything else", () => {
  assert.equal(browserClashCheckSupports("models/plant-a.ifc"), true);
  assert.equal(browserClashCheckSupports("MODELS/PLANT-A.IFC"), true);
  assert.equal(browserClashCheckSupports("models/plant-a.ifcxml"), true);
  assert.equal(browserClashCheckSupports("models/plant-a.stp"), false);
  assert.equal(browserClashCheckSupports(""), false);
});

test("the joint id is the one a server-side detail run re-derives", async () => {
  // THE claim this route stands on. `run_clash_check` takes
  // sha256("<origin>|<sorted member names>")[:12]; a browser that hashed anything else would show
  // a user joints that no worker could then detail.
  const out = await runBrowserClashCheck(
    "models/plant-a.ifc",
    new ArrayBuffer(8),
    {},
    undefined,
    fakeDeps(),
  );
  const expected = createHash("sha256")
    .update("beam-beam|c0|g0|g1")
    .digest("hex")
    .slice(0, 12);
  assert.equal(out.result.joints[0].id, expected);
});

test("the compiled pass's joints become the document the panel already reads", async () => {
  const out = await runBrowserClashCheck(
    "models/plant-a.ifc",
    new ArrayBuffer(8),
    {},
    undefined,
    fakeDeps(),
  );
  assert.equal(out.result.joints.length, 1);
  assert.equal(out.result.joints[0].members.length, 3);
  assert.equal(out.result.groups.length, 1);
  assert.equal(out.result.groups[0].count, 1);
  assert.equal(out.beams, 3);
});

test("the type key comes from the C++, never recomputed here", async () => {
  // Assembly, not classification: a second place deciding what kind of joint this is would be the
  // drift the compiled pass exists to prevent.
  const out = await runBrowserClashCheck(
    "models/plant-a.ifc",
    new ArrayBuffer(8),
    {},
    undefined,
    fakeDeps(),
  );
  assert.equal(
    out.result.joints[0].typeKey,
    "3|BEAM:I:COLUMN+BEAM:I:GIRDER+BEAM:I:GIRDER|perpendicular",
  );
});

test("what did NOT run is stated rather than read as an absence", async () => {
  // "No plate joints" and "plate joints were never looked for" are different answers, and only
  // one of them is a reason to trust the number.
  const out = await runBrowserClashCheck(
    "models/plant-a.ifc",
    new ArrayBuffer(8),
    {},
    undefined,
    fakeDeps(),
  );
  assert.ok(
    out.result.warnings.some((w) =>
      w.includes("plate joints were not looked for"),
    ),
  );
  assert.ok(
    out.result.warnings.some((w) => w.includes("no generators are offered")),
  );
  assert.deepEqual(out.result.joints[0].applicable, []);
});

test("the panel's tolerances reach the compiled pass", async () => {
  await runBrowserClashCheck(
    "models/plant-a.ifc",
    new ArrayBuffer(8),
    { out_of_plane_tol: 0.25, point_tol: 1e-4 },
    undefined,
    fakeDeps(),
  );
  assert.deepEqual(calls[0], { outOfPlaneTol: 0.25, pointTol: 1e-4 });
});
