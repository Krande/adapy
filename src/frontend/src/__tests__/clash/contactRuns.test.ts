// Convex-contact clash runs in the browser (`services/clash/contactRuns.ts`): the run document a
// browser posts must be the one the server accepts (`ada.clash.sidecar.parse_run`) -- the clash ids
// above all, which the server recomputes and refuses on mismatch -- and the flows must store, wait for
// and read back a run, and detail from it, with injected deps.
import assert from "node:assert/strict";
import { test } from "node:test";

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

const {
  contactId,
  contactTargetFor,
  glbKeyForSource,
  groupGeometrySha256,
  memberSoup,
  runBrowserContactFlow,
  runBrowserGroupContactFlow,
  runContactDetailFlow,
  runDocFromColumns,
  runToResultDoc,
  runWorkerContactFlow,
  sha256Hex,
} = await import("@/services/clash/contactRuns");
const { contactRunOf, contactRunOptions, contactRunToResult, groupContactElements } = await import("@/state/clashCheckStore");

const SCOPE = "user:me";
const TARGET = { kind: "file" as const, source_key: "models/frame.ifc" };
const OPTIONS = { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 };

/** What `findGlbContacts` returns for two members and one face contact with one 4-point patch. */
function columns() {
  return {
    memberIds: ["4", "7"],
    memberNames: ["col", "beam"],
    pairs: new Uint32Array([0, 1]),
    near1: new Float64Array([0.2, 0.1, 2.6]),
    near2: new Float64Array([0.198, 0.1, 2.6]),
    normal: new Float64Array([1, 0, 0]),
    penetrationDepth: new Float64Array([-0.002]),
    contactArea: new Float64Array([0.02]),
    patchOffsets: new Uint32Array([0, 1]),
    patchNormals: new Float64Array([1, 0, 0]),
    patchPointOffsets: new Uint32Array([0, 4]),
    patchPoints1: new Float64Array([0.2, 0.05, 2.5, 0.2, 0.15, 2.5, 0.2, 0.15, 2.7, 0.2, 0.05, 2.7]),
    patchPoints2: new Float64Array([0.198, 0.05, 2.5, 0.198, 0.15, 2.5, 0.198, 0.15, 2.7, 0.198, 0.05, 2.7]),
    candidatePairs: 1,
    belowMinArea: 0,
    degenerate: new Uint32Array([]),
  };
}

function meta(over: Record<string, unknown> = {}) {
  return {
    runId: "r1",
    createdAt: "2026-10-14T12:00:00Z",
    target: TARGET,
    glbKey: "_derived/models/frame.ifc.glb",
    glbSha256: "ab".repeat(32),
    upAxis: "z" as const,
    options: OPTIONS,
    ...over,
  };
}

test("contactId is ada.clash.sidecar.contact_id, byte for byte", async () => {
  // Values from Python: contact_id(["4", "7"]), contact_id(["10", "9"]).
  assert.equal(await contactId(["4", "7"]), "9acb47c81a18");
  assert.equal(await contactId(["7", "4"]), "9acb47c81a18", "order-free");
  assert.equal(await contactId(["10", "9"]), "7224bbae3f22", "string sort, as Python sorts");
});

test("runDocFromColumns builds the run document the server parses", async () => {
  const run = await runDocFromColumns(columns(), meta());
  assert.equal(run.schema, "ada.clash/contacts@1");
  assert.equal(run.engine, "wasm");
  assert.deepEqual(run.members, [
    { id: "4", name: "col" },
    { id: "7", name: "beam" },
  ]);
  const [c] = run.contacts;
  assert.equal(c.id, "9acb47c81a18");
  assert.deepEqual(c.members, [0, 1]);
  assert.deepEqual(c.near_points, [
    [0.2, 0.1, 2.6],
    [0.198, 0.1, 2.6],
  ]);
  assert.equal(c.distance, -0.002, "coal's signed distance, kept");
  assert.equal(c.patches.length, 1);
  assert.equal(c.patches[0].points1.length, 4);
  assert.deepEqual(c.patches[0].points2[0], [0.198, 0.05, 2.5]);
  assert.deepEqual(run.geometry, { key: "_derived/models/frame.ifc.glb", sha256: "ab".repeat(32), up_axis: "z" });
});

test("a run reads as the panel's result, grouped by which specs can detail it", async () => {
  const run = await runDocFromColumns(columns(), meta());
  run.contacts[0].applicable = [{ spec: "builtin.girder_gusset", capability: null, tags: ["builtin"], priority: 10 }];
  const result = contactRunToResult(run, "_clash/t/r1.json");
  assert.equal(result.checker, "convex-contact");
  assert.equal(result.joints.length, 1);
  const [j] = result.joints;
  assert.equal(j.origin, "convex-contact");
  assert.deepEqual(
    j.members.map((m) => m.name),
    ["col", "beam"],
  );
  assert.equal(j.contact?.penetrationDepth, 0.002, "the overlap, positive -- core's convention");
  assert.deepEqual(j.applicable.map((a) => a.spec), ["builtin.girder_gusset"]);
  assert.equal(result.groups.length, 1);
  assert.deepEqual(result.groups[0].applicable.map((a) => a.spec), ["builtin.girder_gusset"]);
  assert.equal(result.provenance.run_key, "_clash/t/r1.json");
  // Not yet annotated: one "unassigned" group, and the panel is told the specs are still coming.
  const fresh = runToResultDoc(await runDocFromColumns(columns(), meta()), null) as { groups: { type_key: string }[]; warnings: string[] };
  assert.equal(fresh.groups[0].type_key, "2|contact|unassigned");
  assert.equal(fresh.warnings.length, 1);
});

test("targets and GLB keys", () => {
  assert.equal(glbKeyForSource("models/frame.ifc"), "_derived/models/frame.ifc.glb");
  assert.equal(glbKeyForSource("models/frame.GLB"), "models/frame.GLB");
  assert.deepEqual(contactTargetFor("models/frame.ifc", null), TARGET);
  assert.deepEqual(contactTargetFor(null, { kind: "node", collection: "c", subject: "s", revision: "r1", node: null }), {
    kind: "node",
    collection: "c",
    subject: "s",
    revision: "r1",
  });
  assert.equal(contactTargetFor(null, { kind: "group" }), null, "a group with no saved id has nothing to be filed under");
  assert.deepEqual(contactTargetFor(null, { kind: "group", id: "g1" }), { kind: "group", group_id: "g1" }, "the id alone");
  assert.deepEqual(contactRunOptions(undefined), OPTIONS);
  assert.equal(contactRunOptions({ security_margin: "0.005", min_contact_area: -1 }).security_margin, 0.005);
  assert.equal(contactRunOptions({ min_contact_area: -1 }).min_contact_area, 1e-6, "a negative is not an option");
});

function fakeDeps(over: Record<string, unknown> = {}) {
  const calls: Record<string, unknown[]> = { stored: [], polled: [], detail: [], worker: [] };
  const glb = new TextEncoder().encode("glTF...a viewer GLB").buffer as ArrayBuffer;
  const deps = {
    api: {
      async getBytes() {
        return glb.slice(0);
      },
      async storeRun(_s: string, run: unknown) {
        calls.stored.push(run);
        return { run_key: "_clash/tok/r1.json", summary: {}, job_id: "annotate-1" };
      },
      async getRun(_s: string, key: string) {
        const run = calls.stored[0] as { contacts: { applicable?: unknown[] }[] };
        return { ...run, contacts: run.contacts.map((c) => ({ ...c, applicable: [{ spec: "builtin.girder_gusset" }] })), _key: key };
      },
      async runOnWorker(_s: string, body: unknown) {
        calls.worker.push(body);
        calls.stored.push({ contacts: [] });
        return { job_id: "check-1", run_key: "_clash/tok/w1.json" };
      },
      async detail(_s: string, body: unknown) {
        calls.detail.push(body);
        return { job_id: "detail-1", derived_key: "_derived/clash/contacts-detail/x/result.stats.json", glb_key: "_derived/clash/contacts-detail/x/model.glb" };
      },
      async getJson() {
        return { joints: { n: 1 }, skipped: ["c2: no member named 'x'"] };
      },
    },
    async measure() {
      return columns();
    },
    async jobStatus(jobId: string) {
      calls.polled.push(jobId);
      return { status: "done", error: null };
    },
    wait: () => Promise.resolve(),
    newRunId: () => "r1",
    isoNow: () => "2026-10-14T12:00:00Z",
    ...over,
  };
  return { deps, calls, glb };
}

test("a browser run is measured, stored, annotated and read back", async () => {
  const { deps, calls, glb } = fakeDeps();
  const stages: string[] = [];
  const { run, runKey } = await runBrowserContactFlow(
    deps as never,
    SCOPE,
    { target: TARGET, glbKey: "_derived/models/frame.ifc.glb", upAxis: "z", options: OPTIONS },
    (s) => stages.push(s),
  );
  assert.equal(runKey, "_clash/tok/r1.json");
  const stored = calls.stored[0] as { geometry: { sha256: string }; engine: string };
  assert.equal(stored.engine, "wasm");
  assert.equal(stored.geometry.sha256, await sha256Hex(glb), "the hash of the bytes that were measured");
  assert.deepEqual(calls.polled, ["annotate-1"]);
  assert.equal(run.contacts[0].applicable?.[0].spec, "builtin.girder_gusset", "the annotated run is what comes back");
  assert.deepEqual(stages, ["reading geometry", "checking", "saving", "resolving specs"]);
});

test("a browser run whose specs could not be resolved is still the stored run", async () => {
  const { deps } = fakeDeps({
    async jobStatus() {
      return { status: "error", error: "no worker" };
    },
  });
  const { run, runKey } = await runBrowserContactFlow(deps as never, SCOPE, {
    target: TARGET,
    glbKey: "_derived/models/frame.ifc.glb",
    upAxis: "z",
    options: OPTIONS,
  });
  assert.equal(runKey, "_clash/tok/r1.json");
  assert.equal(run.contacts[0].applicable, undefined);
});

test("a worker run is enqueued, waited for and read back", async () => {
  const { deps, calls } = fakeDeps();
  const { runKey } = await runWorkerContactFlow(deps as never, SCOPE, {
    target: TARGET,
    glbKey: "_derived/models/frame.ifc.glb",
    upAxis: "z",
    options: OPTIONS,
  });
  assert.equal(runKey, "_clash/tok/w1.json");
  assert.deepEqual(calls.worker, [{ target: TARGET, glb_key: "_derived/models/frame.ifc.glb", up_axis: "z", options: OPTIONS }]);
  assert.deepEqual(calls.polled, ["check-1"]);
});

test("a detail is made from the run", async () => {
  const { deps, calls } = fakeDeps();
  const out = await runContactDetailFlow(deps as never, SCOPE, "_clash/tok/r1.json", ["9acb47c81a18"], "builtin.girder_gusset");
  assert.deepEqual(calls.detail, [
    { run_key: "_clash/tok/r1.json", contact_ids: ["9acb47c81a18"], spec: "builtin.girder_gusset" },
  ]);
  assert.equal(out.glbKey, "_derived/clash/contacts-detail/x/model.glb");
  assert.deepEqual(out.stats.skipped, ["c2: no member named 'x'"]);
});

// ── a saved group: several GLBs, one soup ────────────────────────────────────────────────────

const FILE_A = { kind: "file", source_key: "models/a.ifc" } as const;
const NODE_B = { kind: "node", provider: "prov", collection: "plant", subject: "S10", revision: null, node: null } as const;
const GROUP = {
  id: "g1",
  name: "Deck 3",
  members: [
    { target: FILE_A, element: "col", path: ["Site", "col"] },
    { target: NODE_B, element: null, path: [] },
  ],
};

/** `glbMembers` of two GLBs: A holds col (4) and an unrelated x (5); B holds beam (7). A tetrahedron each. */
function membersOf(ids: string[], names: string[], shift: number) {
  const tet = [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1];
  const n = ids.length;
  return {
    ids,
    names,
    vertices: new Float64Array(Array.from({ length: n }, (_, k) => tet.map((c) => c + shift + 10 * k)).flat()),
    vertexOffsets: new Uint32Array(Array.from({ length: n + 1 }, (_, k) => 4 * k)),
    triangles: new Uint32Array(Array.from({ length: n }, () => [0, 1, 2, 0, 1, 3, 0, 2, 3, 1, 2, 3]).flat()),
    triangleOffsets: new Uint32Array(Array.from({ length: n + 1 }, (_, k) => 4 * k)),
  };
}

const GLBS: Record<string, { bytes: string; members: ReturnType<typeof membersOf> }> = {
  "_derived/models/a.ifc.glb": { bytes: "glTF...A", members: membersOf(["4", "5"], ["col", "x"], 0) },
  "_derived/assets/b.glb": { bytes: "glTF...B", members: membersOf(["7"], ["beam"], 0.5) },
};

function groupDeps(over: Record<string, unknown> = {}) {
  const { deps, calls } = fakeDeps();
  const soups: unknown[] = [];
  const read: string[] = [];
  const group = {
    ...deps,
    api: {
      ...deps.api,
      async getBytes(_s: string, key: string) {
        read.push(key);
        return new TextEncoder().encode(GLBS[key].bytes).buffer as ArrayBuffer;
      },
    },
    async glbMembers(glb: ArrayBuffer, upAxis: string) {
      assert.equal(upAxis, "z");
      const text = new TextDecoder().decode(glb);
      return Object.values(GLBS).find((g) => g.bytes === text)!.members;
    },
    async convexContacts(soup: unknown, opts: unknown) {
      soups.push({ soup, opts });
      const { memberIds: _ids, memberNames: _names, ...rest } = columns();
      return rest;
    },
    ...over,
  };
  return { deps: group, calls, soups, read };
}

const GROUP_REQ = {
  target: { kind: "group" as const, group_id: "g1" },
  group: GROUP,
  parts: [
    { glbKey: "_derived/models/a.ifc.glb", source: FILE_A, elements: ["4"] },
    { glbKey: "_derived/assets/b.glb", source: NODE_B, elements: ["7"] },
  ],
  upAxis: "z" as const,
  options: OPTIONS,
  unresolved: ["gone — a.ifc"],
};

test("the group geometry hash is ada.clash.sidecar.group_geometry_sha256", async () => {
  // Value from Python: group_geometry_sha256 of these two parts (in either order).
  const parts = [
    { key: "_derived/b.ifc.glb", sha256: "cd".repeat(32) },
    { key: "_derived/a.ifc.glb", sha256: "ab".repeat(32) },
  ];
  assert.equal(await groupGeometrySha256(parts), "58d4618b8f0bb9e587240a47fb2f984f9be651248a313ea604c92603408b1937");
  assert.equal(await groupGeometrySha256([...parts].reverse()), await groupGeometrySha256(parts));
  // contact_id(["0:4", "1:7"]) -- a group run's qualified member ids.
  assert.equal(await contactId(["0:4", "1:7"]), "6ecce6f6982e");
});

test("memberSoup keeps each part's elements, concatenated with rebased offsets", () => {
  const a = GLBS["_derived/models/a.ifc.glb"].members;
  const b = GLBS["_derived/assets/b.glb"].members;
  const { members, soup, missing } = memberSoup([
    { members: a, keep: new Set(["5", "99"]) },
    { members: b, keep: new Set(["7"]) },
  ]);
  assert.deepEqual(members, [
    { id: "0:5", name: "x", element: "5", part: 0 },
    { id: "1:7", name: "beam", element: "7", part: 1 },
  ]);
  assert.deepEqual(Array.from(soup.vertexOffsets), [0, 4, 8]);
  assert.deepEqual(Array.from(soup.triangleOffsets), [0, 4, 8]);
  assert.deepEqual(Array.from(soup.vertices.slice(0, 3)), [10, 10, 10], "A's second member, not its first");
  assert.deepEqual(Array.from(soup.vertices.slice(12, 15)), [0.5, 0.5, 0.5]);
  assert.deepEqual(Array.from(soup.triangles.slice(12, 15)), [0, 1, 2], "triangle indices stay member-local");
  assert.deepEqual(missing, [["99"], []]);
});

test("a group run reads every part, measures the group's elements as one soup, and is stored as a group run", async () => {
  const { deps, calls, soups, read } = groupDeps();
  const stages: string[] = [];
  const { runKey } = await runBrowserGroupContactFlow(deps as never, SCOPE, GROUP_REQ, (s) => stages.push(s));
  assert.equal(runKey, "_clash/tok/r1.json");
  assert.deepEqual(read, ["_derived/models/a.ifc.glb", "_derived/assets/b.glb"]);
  const { soup, opts } = soups[0] as { soup: { vertexOffsets: Uint32Array }; opts: unknown };
  assert.deepEqual(Array.from(soup.vertexOffsets), [0, 4, 8], "col and beam only -- not x");
  assert.deepEqual(opts, { securityMargin: 0.01, minContactArea: 1e-6, lineContactThickness: 1e-3 });

  const stored = calls.stored[0] as import("@/services/clash/contactRuns").ContactRunDoc;
  assert.deepEqual(stored.target, { kind: "group", group_id: "g1" });
  assert.deepEqual(stored.members, [
    { id: "0:4", name: "col", element: "4", part: 0 },
    { id: "1:7", name: "beam", element: "7", part: 1 },
  ]);
  assert.equal(stored.contacts[0].id, "6ecce6f6982e", "the clash id hashes the qualified member ids");
  const parts = [
    { key: "_derived/models/a.ifc.glb", sha256: await sha256Hex("glTF...A"), source: FILE_A },
    { key: "_derived/assets/b.glb", sha256: await sha256Hex("glTF...B"), source: NODE_B },
  ];
  assert.deepEqual(stored.geometry, { key: "group:g1", sha256: await groupGeometrySha256(parts), up_axis: "z", parts });
  assert.deepEqual(stored.group, GROUP, "the group as measured");
  assert.deepEqual(stored.provenance.unresolved_members, ["gone — a.ifc"]);
  assert.deepEqual(stages, ["reading geometry 1 of 2", "reading geometry 2 of 2", "checking", "saving", "resolving specs"]);
});

test("a group run's result maps each clash member back to its model and element", async () => {
  const { deps, calls } = groupDeps();
  await runBrowserGroupContactFlow(deps as never, SCOPE, GROUP_REQ);
  const run = calls.stored[0] as import("@/services/clash/contactRuns").ContactRunDoc;
  const result = contactRunToResult(run, "_clash/tok/r1.json");
  assert.equal(contactRunOf(result), run);
  assert.equal(result.sourceKey, "group:g1");
  assert.deepEqual(result.joints[0].members.map((m) => m.name), ["col", "beam"]);
  assert.deepEqual(groupContactElements(run, "6ecce6f6982e"), [
    { source: FILE_A, element: "4" },
    { source: NODE_B, element: "7" },
  ]);
  assert.deepEqual(groupContactElements(run, "nope"), []);
  assert.ok(result.warnings.some((w) => w.includes("gone — a.ifc")), "unresolved members are reported");
});

test("a group run refuses a clash module without glbMembers, and a group with under two members", async () => {
  const { deps } = groupDeps({ glbMembers: undefined });
  await assert.rejects(runBrowserGroupContactFlow(deps as never, SCOPE, GROUP_REQ), /glbMembers/);
  const { deps: ok } = groupDeps();
  await assert.rejects(
    runBrowserGroupContactFlow(ok as never, SCOPE, { ...GROUP_REQ, parts: [GROUP_REQ.parts[0]] }),
    /a clash needs two/,
  );
});

test("a failed detail job says so", async () => {
  const { deps } = fakeDeps({
    async jobStatus() {
      return { status: "error", error: "the geometry of this run has changed since it was checked" };
    },
  });
  await assert.rejects(
    runContactDetailFlow(deps as never, SCOPE, "_clash/tok/r1.json", ["x"], "builtin.girder_gusset"),
    /changed since it was checked/,
  );
});
