// Clash CHECKERS and spec PROVIDERS, as the panel reads and sends them. Pins:
//   - an @2 result reads its checker, member roles/ends and typed contact; an @1 one still reads,
//     as core's checker, with an untyped contact's keys kept as extras.
//   - a run with core's checker sends exactly what it always did (no `checker`), so cached results
//     keep their derived keys; a contributed checker sends its name and its advertised defaults.
//   - the spec-provider preference filters AND ranks: a disabled provider's spec is never picked,
//     and the first enabled provider wins over a higher-priority spec from a later one.
//
// Browser globals stubbed before the dynamic import -- see `parseClashResult.test.ts` for why.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BUILTIN_SPEC_PROVIDER,
  parseSpecProviders,
  preferenceFor,
  rankSpecs,
  specProviderChoices,
  toggleSpecProvider,
  withPreference,
} from "@/services/clashSpecProviders";

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

const {
  parseClashResult,
  checkRequestOptions,
  detailBatches,
  specForJoint,
  BUILTIN_CHECKER,
  geometryProviderChoices,
  geometrySourceOf,
  targetProviders,
  UNREADABLE_GEOMETRY_REASON,
} = await import("@/state/clashCheckStore");

function doc(schema: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema,
    source_key: "m.ifc",
    options: {},
    counts: { joints: 1 },
    joints: [
      {
        id: "j1",
        centre: [0, 0, 0],
        members: [
          { name: "a", kind: "BEAM", role: "incoming", end: "end" },
          { name: "b", kind: "BEAM", role: "landing" },
        ],
        type_key: "k",
        type_label: "K",
        applicable: [
          { spec: "builtin.box_joint", priority: 50 },
          { spec: "contrib.box_to_box", capability: "mesh-pool", priority: 10 },
        ],
        contact: { penetration_depth: 0.003, contact_area: 0.0025, extras: { clash_guid: "g" }, patch_area: 1 },
      },
    ],
    groups: [{ type_key: "k", type_label: "K", count: 1, joint_ids: ["j1"] }],
    provenance: {},
    ...extra,
  };
}

test("an @2 result reads its checker, roles, ends and typed contact", () => {
  const r = parseClashResult(doc("ada.clash/result@2", { checker: "mesh-pool", checker_capability: "mesh-pool" }));
  assert.equal(r.checker, "mesh-pool");
  assert.equal(r.checkerCapability, "mesh-pool");
  const j = r.joints[0];
  assert.deepEqual(
    j.members.map((m) => [m.name, m.role, m.end]),
    [
      ["a", "incoming", "end"],
      ["b", "landing", null],
    ],
  );
  assert.equal(j.contact?.penetrationDepth, 0.003);
  assert.equal(j.contact?.contactArea, 0.0025);
  assert.equal(j.contact?.normal, null, "not measured is null, never zero");
  assert.deepEqual(j.contact?.extras, { patch_area: 1, clash_guid: "g" });
});

test("an @1 result still reads, as core's checker", () => {
  const r = parseClashResult(doc("ada.clash/result@1"));
  assert.equal(r.checker, BUILTIN_CHECKER);
  assert.equal(r.checkerCapability, null);
});

test("an unknown schema is still refused", () => {
  assert.throws(() => parseClashResult(doc("ada.clash/result@9")));
});

const core = {
  options: { out_of_plane_tol: 0.1, point_tol: 1e-5, root: "deck", include_plate_joints: true },
  selectedPasses: null,
  checker: BUILTIN_CHECKER,
  checkerOptions: {},
  availableCheckers: [
    { slug: "adapy", name: "adapy", uses_core_options: true },
    {
      slug: "mesh-pool",
      name: "mesh-pool",
      capability: "mesh-pool",
      uses_core_options: false,
      options: [
        { key: "security_margin", type: "number" as const, default: 0.01 },
        { key: "min_contact_area", type: "number" as const, default: 1e-6 },
      ],
    },
  ],
};

test("core's checker sends what it always sent -- no checker field, so cache keys hold", () => {
  assert.deepEqual(checkRequestOptions(core), core.options);
  assert.deepEqual(checkRequestOptions({ ...core, selectedPasses: ["beam-beam"] }), {
    ...core.options,
    passes: ["beam-beam"],
  });
});

test("a contributed checker sends its name, its defaults, and drops core tolerances it ignores", () => {
  const sent = checkRequestOptions({
    ...core,
    checker: "mesh-pool",
    checkerOptions: { "mesh-pool": { security_margin: 0.02 } },
  });
  assert.deepEqual(sent, {
    root: "deck",
    checker: "mesh-pool",
    checker_options: { security_margin: 0.02, min_contact_area: 1e-6 },
  });
});

test("a geometry provider is sent only when chosen, so a default run keeps its cache key", () => {
  assert.deepEqual(checkRequestOptions({ ...core, geometryProvider: null }), core.options);
  assert.ok(!("geometry_provider" in checkRequestOptions({ ...core, geometryProvider: "" })));
  assert.deepEqual(checkRequestOptions({ ...core, geometryProvider: "member-reader" }), {
    ...core.options,
    geometry_provider: "member-reader",
  });
  // It is a fact about the MODEL, not the engine: a contributed checker carries it too.
  assert.equal(
    checkRequestOptions({ ...core, checker: "mesh-pool", geometryProvider: "member-reader" }).geometry_provider,
    "member-reader",
  );
});

test("the geometry choice offers readable providers and explains the target's unreadable ones", () => {
  const members = [
    { target: { kind: "node" as const, provider: "mesh-only", collection: "c", subject: "s", revision: null, node: "n" }, element: "/A", path: [] },
    { target: { kind: "file" as const, source_key: "a.ifc" }, element: null, path: [] },
  ];
  const group = { kind: "group" as const, name: "g", members };
  assert.deepEqual(targetProviders(group), ["mesh-only"]);
  assert.deepEqual(targetProviders({ kind: "file", sourceKey: "a.ifc" }), []);
  assert.deepEqual(
    targetProviders({ kind: "node", collection: "c", subject: "s", provider: "mesh-only", collectionProviders: ["member-reader", "mesh-only"] }),
    ["member-reader", "mesh-only"],
  );

  const choices = geometryProviderChoices(group, [
    { id: "member-reader", label: "Members", readable: true },
    { id: "broken", readable: false, unavailable_reason: "dependency missing" },
  ]);
  assert.deepEqual(
    choices.map((c) => [c.id, c.readable, c.reason]),
    [
      ["member-reader", true, null],
      ["broken", false, "dependency missing"],
      ["mesh-only", false, UNREADABLE_GEOMETRY_REASON],
    ],
  );
  // Unknown is not "unreadable": nothing is offered until the list has been fetched.
  assert.deepEqual(geometryProviderChoices(group, null), []);
});

test("the result says where its geometry came from, and nothing when it was each member's own", () => {
  assert.equal(geometrySourceOf(parseClashResult(doc("ada.clash/result@2"))), null);
  const r = parseClashResult(
    doc("ada.clash/result@2", {
      provenance: { geometry_provider: "member-reader", geometry_remap: [{}, {}, {}], geometry_unmatched: [{}] },
    }),
  );
  assert.deepEqual(geometrySourceOf(r), { provider: "member-reader", remapped: 3, unmatched: 1 });
});

test("spec providers: unrestricted ranks by priority; a preference filters and ranks", () => {
  const r = parseClashResult(doc("ada.clash/result@2"));
  const j = r.joints[0];
  assert.equal(specForJoint(j)?.spec, "builtin.box_joint", "priority alone");
  assert.equal(specForJoint(j, ["mesh-pool", BUILTIN_SPEC_PROVIDER])?.spec, "contrib.box_to_box", "provider rank first");
  assert.equal(specForJoint(j, [BUILTIN_SPEC_PROVIDER])?.spec, "builtin.box_joint");
  assert.equal(specForJoint(j, []), null, "[] enables nothing");
  assert.deepEqual(
    detailBatches(r, null, ["mesh-pool"]).map((b) => [b.spec.spec, b.jointIds]),
    [["contrib.box_to_box", ["j1"]]],
  );
});

test("the stored setting: missing is unrestricted, [] is nothing, malformed is ignored", () => {
  const map = parseSpecProviders('{"shared": ["mesh-pool"], "project:1": [], "project:2": "bad"}');
  assert.deepEqual(preferenceFor(map, "shared"), ["mesh-pool"]);
  assert.deepEqual(preferenceFor(map, "project:1"), []);
  assert.equal(preferenceFor(map, "project:2"), null);
  assert.equal(preferenceFor(map, "project:3"), null);
  assert.deepEqual(parseSpecProviders("not json"), {});
  assert.equal(preferenceFor(withPreference(map, "shared", null), "shared"), null);
});

test("toggling one provider off from unrestricted keeps every other", () => {
  assert.deepEqual(toggleSpecProvider(null, ["mesh-pool", "x"], "x", false), [BUILTIN_SPEC_PROVIDER, "mesh-pool"]);
  const choices = specProviderChoices(["mesh-pool"], ["gone", BUILTIN_SPEC_PROVIDER]);
  assert.deepEqual(
    choices.map((c) => [c.provider, c.enabled, c.advertised]),
    [
      ["gone", true, false],
      [BUILTIN_SPEC_PROVIDER, true, true],
      ["mesh-pool", false, true],
    ],
  );
  assert.deepEqual(
    rankSpecs([{ spec: "s", capability: null, priority: 0 }], ["mesh-pool"]),
    [],
    "a spec from a provider left out is not offered",
  );
});
