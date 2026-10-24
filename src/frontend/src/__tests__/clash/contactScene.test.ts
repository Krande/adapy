// A convex-contact run in the scene (`utils/scene/clashContactPlan.ts`): which draw ranges of which
// loaded model "fade the rest" keeps -- by element id, per model, every other loaded model faded --
// and the contacts overlay: what is drawn for each contact, in which colour, through the panel's
// filters, and in which frame (the transform the joint markers take).
import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string) => (memory.has(k) ? memory.get(k)! : null),
  setItem: (k: string, v: string) => void memory.set(k, String(v)),
  removeItem: (k: string) => void memory.delete(k),
  clear: () => memory.clear(),
  key: () => null,
  get length() {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { contactRunToResult, DEFAULT_CLASH_FILTERS } = await import("@/state/clashCheckStore");
const plan = await import("@/utils/scene/clashContactPlan");
const { fillBuffers } = await import("@/utils/scene/clashContactOverlay");
type ContactRunDoc = import("@/services/clash/contactRuns").ContactRunDoc;
type Vec3 = [number, number, number];

const OPTIONS = { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 };

/** A square patch in the plane x = `x`, 0.1 x 0.2, on both members (`gap` apart along x). */
function patch(x: number, gap = 0.002) {
  const sq = (px: number): Vec3[] => [
    [px, 0.05, 2.5],
    [px, 0.15, 2.5],
    [px, 0.15, 2.7],
    [px, 0.05, 2.7],
    [px, 0.05, 2.5],
  ];
  return { normal: [1, 0, 0] as Vec3, points1: sq(x), points2: sq(x - gap) };
}

function contact(id: string, members: [number, number], roles?: { incoming: number; landing: number }) {
  return {
    id,
    members,
    near_points: [
      [0.2, 0.1, 2.6],
      [0.198, 0.1, 2.6],
    ] as [Vec3, Vec3],
    normal: [1, 0, 0] as Vec3,
    distance: -0.002,
    contact_area: 0.02,
    patches: [patch(0.2)],
    ...(roles ? { roles } : {}),
  };
}

const FILE_A = { kind: "file" as const, source_key: "models/a.ifc" };
const NODE_B = { kind: "node" as const, provider: "prov", collection: "plant", subject: "S1", revision: "r1", node: null };

/** A saved group's run: element "4" of model A, elements "4" and "9" of node B -- "4" twice, and two
 *  members named "col", which is why nothing here may go by name. */
function groupRun(): ContactRunDoc {
  return {
    schema: "ada.clash/contacts@1",
    run_id: "r1",
    created_at: "2026-10-16T12:00:00Z",
    engine: "wasm",
    checker: "convex-contact",
    target: { kind: "group", group_id: "selection-0123456789abcdef" },
    geometry: {
      key: "group:selection-0123456789abcdef",
      sha256: "00",
      up_axis: "z",
      parts: [
        { key: "_derived/models/a.ifc.glb", sha256: "aa", source: FILE_A },
        { key: "_derived/b/model.glb", sha256: "bb", source: NODE_B },
      ],
    },
    options: OPTIONS,
    members: [
      { id: "0:4", name: "col", element: "4", part: 0 },
      { id: "1:4", name: "col", element: "4", part: 1 },
      { id: "1:9", name: "beam", element: "9", part: 1 },
    ],
    contacts: [contact("c01", [0, 2], { incoming: 2, landing: 0 }), contact("c12", [1, 2])],
    provenance: {},
    group: { id: "selection-0123456789abcdef", name: "selection (2 members)", members: [] },
  };
}

function fileRun(): ContactRunDoc {
  return {
    ...groupRun(),
    target: FILE_A,
    geometry: { key: "_derived/models/a.ifc.glb", sha256: "aa", up_axis: "z" },
    members: [
      { id: "4", name: "col" },
      { id: "7", name: "col" },
    ],
    contacts: [contact("c47", [0, 1], { incoming: 1, landing: 0 })],
    group: undefined,
  };
}

const LOADED = ["models/a.ifc", "assets:prov/plant/S1@r2", "models/unrelated.ifc"];

test("a contact's members carry the roles the worker resolved", () => {
  const result = contactRunToResult(groupRun(), "_clash/x/r1.json");
  assert.deepEqual(
    result.jointsById.get("c01")!.members.map((m) => [m.name, m.role]),
    [
      ["col", "landing"],
      ["beam", "incoming"],
    ],
  );
  assert.deepEqual(
    result.jointsById.get("c12")!.members.map((m) => m.role),
    [null, null],
  );
});

test("a group run's loaded models are matched per part, a node at any revision", () => {
  const run = groupRun();
  assert.deepEqual(plan.loadedSourcesByPart(run, LOADED, []), [["models/a.ifc"], ["assets:prov/plant/S1@r2"]]);
  assert.equal(plan.contactSpaceSource(run, LOADED, []), "models/a.ifc");
  assert.equal(plan.contactSpaceSource(run, ["assets:prov/plant/S1@r2"], []), "assets:prov/plant/S1@r2");
  assert.equal(plan.contactSpaceSource(run, ["models/unrelated.ifc"], []), null);
});

test("fade the rest keeps the focused contact's elements per model and fades every other loaded model", () => {
  const run = groupRun();
  const result = contactRunToResult(run, null);
  const keep = plan.contactKeepByPart(run, result, "c01", null);
  assert.deepEqual(
    [...keep!].map(([p, s]) => [p, [...s]]),
    [
      [0, ["4"]],
      [1, ["9"]],
    ],
  );
  const targets = plan.contactIsolationTargets(run, keep, LOADED, []);
  assert.deepEqual(
    targets.map((t) => [t.sourceName, [...t.keep].sort()]),
    [
      ["models/a.ifc", ["4"]],
      // Element "4" of B is NOT kept: it is a different member from A's "4".
      ["assets:prov/plant/S1@r2", ["9"]],
      // A model with no member of the contact is the rest too: faded whole.
      ["models/unrelated.ifc", []],
    ],
  );
});

test("an open group keeps all its contacts' members; no cursor, or nothing loaded, isolates nothing", () => {
  const run = groupRun();
  const result = contactRunToResult(run, null);
  const typeKey = result.jointsById.get("c12")!.typeKey;
  const keep = plan.contactKeepByPart(run, result, null, typeKey);
  assert.deepEqual(
    plan.contactIsolationTargets(run, keep, LOADED, []).map((t) => [t.sourceName, [...t.keep].sort()]),
    [
      ["models/a.ifc", ["4"]],
      ["assets:prov/plant/S1@r2", ["4", "9"]],
      ["models/unrelated.ifc", []],
    ],
  );
  assert.equal(plan.contactKeepByPart(run, result, null, null), null);
  assert.deepEqual(plan.contactIsolationTargets(run, null, LOADED, []), []);
  // The members' models are not loaded: fading everything because nothing resolved would look broken.
  assert.deepEqual(plan.contactIsolationTargets(run, keep, ["models/unrelated.ifc"], []), []);
});

test("a file run isolates and selects by element id, two members of one name told apart", () => {
  const run = fileRun();
  const result = contactRunToResult(run, null);
  const keep = plan.contactKeepByPart(run, result, "c47", null);
  assert.deepEqual(
    plan.contactIsolationTargets(run, keep, ["models/a.ifc", "models/b.ifc"], []).map((t) => [t.sourceName, [...t.keep].sort()]),
    [
      ["models/a.ifc", ["4", "7"]],
      ["models/b.ifc", []],
    ],
  );
  assert.deepEqual(plan.contactSceneElements(run, ["c47"], ["models/a.ifc"], []), [
    { sourceName: "models/a.ifc", element: "4" },
    { sourceName: "models/a.ifc", element: "7" },
  ]);
  // A node run matches its node without naming a provider.
  const nodeRun = { ...run, target: { kind: "node" as const, collection: "plant", subject: "S1" } };
  assert.deepEqual(plan.loadedSourcesByPart(nodeRun, LOADED, []), [["assets:prov/plant/S1@r2"]]);
});

const SHOW = {
  filters: DEFAULT_CLASH_FILTERS,
  hiddenOrigins: [] as string[],
  selectedGroup: null,
  selectedJoint: null,
  showContacts: true,
};

test("the overlay draws each patch on both members in their role colours, and the normal from its centroid", () => {
  const run = groupRun();
  const result = contactRunToResult(run, null);
  const contacts = plan.contactOverlayPlan({ ...SHOW, result }, run, { modelDiagonal: 10 });
  assert.deepEqual(
    contacts.map((c) => c.id),
    ["c01", "c12"],
  );
  const [resolved, unresolved] = contacts;
  // Member i (index 0, landing) behind the contact plane, member j (index 2, incoming) in front.
  assert.deepEqual(
    resolved.polygons.map((p) => [p.role, p.color]),
    [
      ["landing", plan.ROLE_COLOURS.landing],
      ["incoming", plan.ROLE_COLOURS.incoming],
    ],
  );
  assert.deepEqual(
    unresolved.polygons.map((p) => p.color),
    [plan.ROLE_COLOURS.unresolved, plan.ROLE_COLOURS.unresolved],
  );
  // The midpoint polygon (x = 0.199), four corners (the closing repeat dropped), nudged 1% of the
  // patch apart -- landing side towards -x, incoming side towards +x.
  const [behind, front] = resolved.polygons;
  assert.equal(behind.points.length, 4);
  const spread = Math.hypot(0.1, 0.2);
  for (const p of behind.points) assert.ok(Math.abs(p[0] - (0.199 - spread * 0.01)) < 1e-12);
  for (const p of front.points) assert.ok(Math.abs(p[0] - (0.199 + spread * 0.01)) < 1e-12);
  // The arrow: from the patch centroid along the normal (i -> j), 1.5 x the patch, within 1-5% of the model.
  const { from, to } = resolved.arrow!;
  assert.deepEqual(
    from.map((v) => +v.toFixed(9)),
    [0.199, 0.1, 2.6],
  );
  assert.ok(Math.abs(to[0] - from[0] - Math.max(0.1, spread * 1.5)) < 1e-12 && to[1] === from[1] && to[2] === from[2]);
  assert.equal(plan.arrowLength(spread, 1000), 10, "a tiny patch on a plant still gets 1% of the model");
  assert.equal(plan.arrowLength(5, 10), 0.5, "and a big patch no more than 5%");
  // Every contact emphasised while nothing is selected; the focused one alone once one is.
  assert.ok(contacts.every((c) => c.emphasised));
  const focused = plan.contactOverlayPlan({ ...SHOW, result, selectedJoint: "c12" }, run, { modelDiagonal: 10 });
  assert.deepEqual(
    focused.map((c) => [c.id, c.emphasised, c.focused]),
    [
      ["c01", false, false],
      ["c12", true, true],
    ],
  );
  // What a click on a triangle resolves to is the contact that triangle was drawn for.
  const { ids, positions } = fillBuffers(contacts);
  assert.deepEqual(ids, ["c01", "c01", "c01", "c01", "c12", "c12", "c12", "c12"]);
  assert.equal(positions.length, ids.length * 9);
});

test("the overlay shows exactly what the panel's filters leave", () => {
  const run = groupRun();
  run.contacts[0].applicable = [{ spec: "builtin.girder_gusset", capability: null, tags: [], priority: 1 }];
  const result = contactRunToResult(run, null);
  const shown = (s: Partial<typeof SHOW>) =>
    plan.contactOverlayPlan({ ...SHOW, ...s, result }, run, { modelDiagonal: 10 }).map((c) => c.id);
  assert.deepEqual(shown({}), ["c01", "c12"]);
  assert.deepEqual(shown({ filters: { ...DEFAULT_CLASH_FILTERS, applicable: "matched" } }), ["c01"]);
  assert.deepEqual(shown({ filters: { ...DEFAULT_CLASH_FILTERS, typeKey: result.jointsById.get("c12")!.typeKey } }), ["c12"]);
  assert.deepEqual(shown({ hiddenOrigins: ["convex-contact"] }), []);
  assert.deepEqual(shown({ showContacts: false }), []);
  // Toggle off, a clash picked from the list: that contact alone.
  const picked = plan.contactOverlayPlan({ ...SHOW, showContacts: false, selectedJoint: "c12", result }, run, {
    modelDiagonal: 10,
  });
  assert.deepEqual(
    picked.map((c) => c.id),
    ["c12"],
  );
  assert.deepEqual(plan.contactOverlayPlan({ ...SHOW, result }, null, { modelDiagonal: 10 }), []);
  // An open group dims the others rather than dropping them.
  const typeKey = result.jointsById.get("c01")!.typeKey;
  assert.deepEqual(
    plan.contactOverlayPlan({ ...SHOW, result, selectedGroup: typeKey }, run, { modelDiagonal: 10 }).map((c) => [c.id, c.emphasised]),
    [
      ["c01", true],
      ["c12", false],
    ],
  );
});

test("placed by the loaded model's source -> world transform, the overlay lands on the member it measured", () => {
  // The scene as `setupModelLoader` builds it: the registered wrapper group, and inside it the glTF
  // scene node carrying the recentring offset (`modelStore.translation`) -- the node the markers and
  // the overlay take their matrix from. The member is a box mesh at its own model coordinates.
  const wrapper = new THREE.Group();
  const gltfScene = new THREE.Group();
  gltfScene.userData.__sourceSpaceRoot = true;
  const translation = new THREE.Vector3(-288.5, -109.0, -500.0);
  gltfScene.position.add(translation);
  wrapper.add(gltfScene);
  const memberLo = new THREE.Vector3(288.415, 108.9, 503.4);
  const memberHi = new THREE.Vector3(288.585, 109.1, 503.61);
  const geom = new THREE.BoxGeometry(memberHi.x - memberLo.x, memberHi.y - memberLo.y, memberHi.z - memberLo.z);
  geom.translate((memberLo.x + memberHi.x) / 2, (memberLo.y + memberHi.y) / 2, (memberLo.z + memberHi.z) / 2);
  const member = new THREE.Mesh(geom);
  gltfScene.add(member);
  wrapper.updateMatrixWorld(true);

  // A contact on the member's top face, as a run stores it: model frame, untranslated.
  const top = 503.6099853515625;
  const sq: Vec3[] = [
    [288.585, 109.1, top],
    [288.585, 108.9, top],
    [288.415, 108.9, top],
    [288.415, 109.1, top],
  ];
  const run = fileRun();
  run.contacts = [
    {
      ...contact("c47", [0, 1], { incoming: 1, landing: 0 }),
      near_points: [
        [288.4964, 109.0805, top],
        [288.4964, 109.0805, top],
      ],
      normal: [0, 0, -1],
      patches: [{ normal: [0, 0, -1], points1: sq, points2: sq }],
    },
  ];
  const [c] = plan.contactOverlayPlan({ ...SHOW, result: contactRunToResult(run, null) }, run, {
    modelDiagonal: 100,
    matrix: gltfScene.matrixWorld.elements,
  });
  const box = new THREE.Box3().setFromObject(member).expandByScalar(0.01);
  for (const poly of c.polygons) for (const p of poly.points) assert.ok(box.containsPoint(new THREE.Vector3(...p)), `${p} on the member`);
  assert.ok(box.containsPoint(new THREE.Vector3(...c.arrow!.from)));
  // ... and NOT where the wrapper's (identity) matrix would have put it: a model-sized offset away.
  const unplaced = plan.contactOverlayPlan({ ...SHOW, result: contactRunToResult(run, null) }, run, {
    modelDiagonal: 100,
    matrix: wrapper.matrixWorld.elements,
  })[0];
  assert.ok(!box.containsPoint(new THREE.Vector3(...unplaced.arrow!.from)));
});
