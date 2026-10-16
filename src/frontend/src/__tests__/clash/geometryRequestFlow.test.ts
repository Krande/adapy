// A clash check through a geometry provider that lacks some members: plan first, ask, request
// exactly the missing members through the shared group request, re-plan, then check -- driven with
// fakes for every dependency (`runGeometryAwareCheckFlow`).
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

const {
  contactGeometryProvider,
  runGeometryAwareAnnotateFlow,
  runGeometryAwareCheckFlow,
  geometryRequestPrompt,
  geometryPromptSentence,
} = await import("@/state/clashCheckStore");
type Deps = Parameters<typeof runGeometryAwareCheckFlow>[0];
type AnnotateDeps = Parameters<typeof runGeometryAwareAnnotateFlow>[0];
type ContactRun = Awaited<ReturnType<AnnotateDeps["contact"]["api"]["getRun"]>>;
type Plan = Awaited<ReturnType<Deps["geometryPlan"]>>;
type Answer = Awaited<ReturnType<Deps["prompt"]>>;
type Prompt = Parameters<Deps["prompt"]>[0];
type NodeRequest = NonNullable<Awaited<ReturnType<Deps["nodeRequestFor"]>>>;

const PROVIDER = "member-reader";
const SCOPE = "user:me";

const meshTarget = (subject: string, node: string | null = null) => ({
  kind: "node" as const,
  provider: "mesh-only",
  collection: "plant",
  subject,
  revision: "r2",
  node,
});
const ELEMENT = { target: meshTarget("fnzone"), element: "/LOST", path: ["/LOST"] };
const WHOLE = { target: meshTarget("fnorphan", "fnorphan"), element: null, path: [] };
const MATCHED = { target: meshTarget("fnzone"), element: "/Z100-BEAMS2", path: ["/Z100-BEAMS2"] };
const TARGET = { kind: "group" as const, name: "Deck 3", members: [ELEMENT, WHOLE, MATCHED] };
const OPTIONS = { include_plate_joints: false, geometry_provider: PROVIDER };

const RESULT_DOC = {
  schema: "ada.clash/result@2",
  source_key: "group:t",
  options: {},
  counts: { members: 2, joints: 0 },
  joints: [],
  groups: [],
  provenance: {},
};

function planOf(
  unmatched: readonly { member: unknown; reason: string; label?: string | null; row?: string | null }[],
): Plan {
  return {
    geometry_provider: PROVIDER,
    matched: [{ from: MATCHED, to: { ...MATCHED, target: { ...MATCHED.target, provider: PROVIDER } } }],
    unmatched,
    unchanged: [],
  } as unknown as Plan;
}

const MISSING = [
  { member: ELEMENT, reason: "no node of provider 'member-reader' in 'plant' is named '/LOST'; left out", label: "/LOST" },
  { member: WHOLE, reason: "no node of provider 'member-reader' in 'plant' is named '/Z100-ORPHAN'", label: "/Z100-ORPHAN" },
];

const NODE_REQUEST: NodeRequest = {
  pluginId: "geometry-fetch",
  options: { action: "fetch" },
  collectionOption: "collection",
  label: "Fetch geometry",
  requiresAdmin: false,
  nodeOption: "nodes",
  labelOption: "labels",
  maxNodes: 5,
};

interface Recorder {
  calls: string[];
  prompts: Prompt[];
  pluginJobs: { pluginId: string; options: Record<string, unknown> }[];
  checks: number;
  tracked: string[];
}

function fakes(opts: {
  plans: Plan[];
  answer?: Answer;
  nodeRequest?: NodeRequest | null;
  isAdmin?: boolean;
  rows?: ReadonlyMap<string, string>;
}): { deps: Deps; rec: Recorder } {
  const rec: Recorder = { calls: [], prompts: [], pluginJobs: [], checks: 0, tracked: [] };
  const plans = [...opts.plans];
  let job = 0;
  const deps: Deps = {
    api: {
      async runClashCheck() {
        rec.calls.push("check");
        rec.checks += 1;
        return { job_id: "check-1", derived_key: "_derived/clash/group/x/result.json", cached: false };
      },
      async runClashDetail() {
        throw new Error("not used");
      },
      async getClashResult() {
        return RESULT_DOC;
      },
      async getDetailStats() {
        return {};
      },
      async jobStatus() {
        return { status: "done", error: null };
      },
    },
    trackJob: (o) => rec.tracked.push(o.label),
    wait: async () => {},
    async geometryPlan(scope, target, provider) {
      assert.equal(scope, SCOPE);
      assert.equal(target, TARGET);
      assert.equal(provider, PROVIDER);
      rec.calls.push("plan");
      const next = plans.shift();
      assert.ok(next, "planned more often than the test expected");
      return next;
    },
    async nodeRequestFor(provider) {
      assert.equal(provider, PROVIDER);
      return opts.nodeRequest === undefined ? NODE_REQUEST : opts.nodeRequest;
    },
    isAdmin: opts.isAdmin ?? false,
    async prompt(q) {
      rec.calls.push("prompt");
      rec.prompts.push(q);
      return opts.answer ?? "request";
    },
    async resolveRows(_scope, members) {
      rec.calls.push(`rows:${members.length}`);
      return opts.rows ?? new Map();
    },
    request: {
      api: {
        async pluginJob(pluginId, body) {
          rec.calls.push("request");
          rec.pluginJobs.push({ pluginId, options: body.options });
          job += 1;
          return { job_id: `fetch-${job}`, derived_key: `fetch-${job}.json` };
        },
        async publish() {
          job += 1;
          return { job_id: `publish-${job}`, derived_key: `publish-${job}.json` };
        },
        async jobStatus() {
          return { status: "done", error: null };
        },
        async readJson(_scope, key) {
          return key.startsWith("fetch") ? { asset_staging_id: `stage-${key}` } : { collection: "plant", revision: "r3" };
        },
      },
      trackJob: (o) => rec.tracked.push(o.label),
      wait: async () => {},
    },
    afterRequest: () => {
      rec.calls.push("refresh");
    },
  };
  return { deps, rec };
}

test("missing members: prompt, YES -> request exactly them -> re-plan -> check", async () => {
  // The element member resolves to a published row; the whole node is its own id.
  const { memberKey } = await import("@/utils/groups/savedGroups");
  const rows = new Map([[memberKey(ELEMENT as never), "fn04"]]);
  const { deps, rec } = fakes({ plans: [planOf(MISSING), planOf([])], rows });

  const out = await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS);
  assert.ok(out);
  // Only the element is looked up in the tree; a whole node is its own id.
  assert.deepEqual(rec.calls, ["plan", "prompt", "rows:1", "request", "refresh", "plan", "check"]);

  const [prompt] = rec.prompts;
  assert.equal(prompt.kind, "offer");
  assert.equal(prompt.count, 2);
  assert.equal(prompt.provider, PROVIDER);
  assert.match(geometryPromptSentence(prompt), /2 members have no geometry at member-reader/);
  assert.match(geometryPromptSentence(prompt), /may take several minutes/);

  // ONE request (maxNodes 5) naming exactly the two missing nodes -- not the matched member -- with
  // the labels the plan looked them up under, aligned with the ids.
  assert.equal(rec.pluginJobs.length, 1);
  const { pluginId, options } = rec.pluginJobs[0];
  assert.equal(pluginId, "geometry-fetch");
  assert.equal(options.collection, "plant");
  assert.deepEqual(options.nodes, ["fn04", "fnorphan"]);
  assert.deepEqual(options.labels, ["/LOST", "/Z100-ORPHAN"]);
  // The jobs go to the toast, the request's and the check's.
  assert.ok(rec.tracked.some((l) => l.startsWith("Fetch geometry")));
  assert.ok(rec.tracked.some((l) => l.startsWith("Clash check")));

  assert.ok(out.notes.some((n) => n.includes("1 request published")), out.notes.join(" | "));
  assert.ok(out.notes.some((n) => n.includes("Every member is now at member-reader")), out.notes.join(" | "));
  assert.equal(out.derivedKey, "_derived/clash/group/x/result.json");
});

test("a row the plan already placed is requested as it is, with no tree walk", async () => {
  // The server read the member's own hierarchy: the browser's tree (which may not hold that level)
  // is not asked at all.
  const placed = [{ ...MISSING[0], row: "fn04" }, MISSING[1]];
  const { deps, rec } = fakes({ plans: [planOf(placed), planOf([])] });

  const out = await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS);
  assert.ok(out);
  assert.deepEqual(rec.calls, ["plan", "prompt", "request", "refresh", "plan", "check"]);
  assert.deepEqual(rec.pluginJobs[0].options.nodes, ["fn04", "fnorphan"]);
});

test("members still missing after the request are reported, and the check runs anyway", async () => {
  const { deps, rec } = fakes({ plans: [planOf(MISSING), planOf(MISSING.slice(1))] });
  const out = await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS);
  assert.ok(out);
  assert.equal(rec.checks, 1);
  // The element member could not be placed in the tree, so only the whole node was requested.
  assert.deepEqual(rec.pluginJobs[0].options.nodes, ["fnorphan"]);
  assert.ok(out.notes.some((n) => n.includes("could not be requested")), out.notes.join(" | "));
  assert.ok(out.notes.some((n) => n.includes("1 member is still not at member-reader")), out.notes.join(" | "));
});

test("a quick (on-demand) request is worded as quick", () => {
  const prompt = geometryRequestPrompt(planOf(MISSING), { ...NODE_REQUEST, onDemand: true }, false);
  assert.ok(prompt);
  assert.match(geometryPromptSentence(prompt), /quick/);
  assert.doesNotMatch(geometryPromptSentence(prompt), /several minutes/);
});

test("prompt NO: nothing is requested and the check runs with the members left out", async () => {
  const { deps, rec } = fakes({ plans: [planOf(MISSING)], answer: "skip" });
  const out = await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS);
  assert.ok(out);
  assert.deepEqual(rec.calls, ["plan", "prompt", "check"]);
  assert.equal(rec.pluginJobs.length, 0);
  assert.ok(out.notes.some((n) => n.includes("2 members not at member-reader left out")));
});

test("prompt CANCEL: nothing is requested and nothing is checked", async () => {
  const { deps, rec } = fakes({ plans: [planOf(MISSING)], answer: "cancel" });
  assert.equal(await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS), null);
  assert.deepEqual(rec.calls, ["plan", "prompt"]);
});

test("a provider that takes no node requests is explained, and no request is offered", async () => {
  // Even answered "request", a provider with no node request is never asked.
  const { deps, rec } = fakes({ plans: [planOf(MISSING)], nodeRequest: null, answer: "request" });
  const out = await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS);
  assert.ok(out);
  assert.equal(rec.prompts[0].kind, "no-request");
  assert.match(geometryPromptSentence(rec.prompts[0]), /takes no node requests/);
  assert.equal(rec.pluginJobs.length, 0);
  assert.deepEqual(rec.calls, ["plan", "prompt", "check"]);
});

test("an admin-only request is refused to a non-admin, offered to an admin", async () => {
  const adminOnly = { ...NODE_REQUEST, requiresAdmin: true };
  const user = fakes({ plans: [planOf(MISSING)], nodeRequest: adminOnly, answer: "request" });
  await runGeometryAwareCheckFlow(user.deps, SCOPE, TARGET, OPTIONS);
  assert.equal(user.rec.prompts[0].kind, "admin-only");
  assert.match(geometryPromptSentence(user.rec.prompts[0]), /Only an administrator/);
  assert.equal(user.rec.pluginJobs.length, 0);

  const admin = fakes({ plans: [planOf(MISSING), planOf([])], nodeRequest: adminOnly, isAdmin: true });
  await runGeometryAwareCheckFlow(admin.deps, SCOPE, TARGET, OPTIONS);
  assert.equal(admin.rec.prompts[0].kind, "offer");
  assert.equal(admin.rec.pluginJobs.length, 1);
});

test("every member matched: no prompt, straight to the check", async () => {
  const { deps, rec } = fakes({ plans: [planOf([])] });
  const out = await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, OPTIONS);
  assert.ok(out);
  assert.deepEqual(rec.calls, ["plan", "check"]);
  assert.deepEqual(out.notes, []);
});

test("no geometry provider, or a file: no plan at all", async () => {
  const { deps, rec } = fakes({ plans: [] });
  await runGeometryAwareCheckFlow(deps, SCOPE, TARGET, { include_plate_joints: false });
  await runGeometryAwareCheckFlow(deps, SCOPE, { kind: "file", sourceKey: "a.ifc" }, OPTIONS);
  assert.deepEqual(rec.calls, ["check", "check"]);
});

// ── the convex-contact run: the same step before ANNOTATING a stored run ──────────────────────

const RUN_KEY = "_clash/abc/r1.json";

function storedRun(provenance: Record<string, unknown>): ContactRun {
  return {
    schema: "ada.clash/contacts@1",
    run_id: "r1",
    created_at: "2026-10-16T12:00:00Z",
    engine: "wasm",
    target: { kind: "group", group_id: "g1" },
    geometry: { key: "group:g1", sha256: "x", up_axis: "z", parts: [] },
    options: { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 },
    members: [],
    contacts: [],
    provenance,
  } as unknown as ContactRun;
}

/** The step's fakes plus the run's REST deps; every annotate is recorded with its provider. */
function annotateFakes(opts: Parameters<typeof fakes>[0] & { annotateStatus?: string }) {
  const { deps: check, rec } = fakes(opts);
  const annotated: (string | null)[] = [];
  const { api: _api, trackJob: _t, wait: _w, now: _n, ...step } = check;
  const deps: AnnotateDeps = {
    ...step,
    contact: {
      api: {
        async runOnWorker() {
          throw new Error("not used");
        },
        async storeRun() {
          throw new Error("not used");
        },
        async annotate(_scope, runKey, provider) {
          assert.equal(runKey, RUN_KEY);
          rec.calls.push(`annotate:${provider ?? "own"}`);
          annotated.push(provider ?? null);
          return { job_id: "annotate-1", geometry_provider: provider };
        },
        async getRun(_scope, key) {
          assert.equal(key, RUN_KEY);
          rec.calls.push("read");
          return storedRun({ applicability: { resolved: 3, unbound: 0 }, geometry_provider: PROVIDER });
        },
        async detail() {
          throw new Error("not used");
        },
        async getBytes() {
          throw new Error("not used");
        },
        async getJson() {
          throw new Error("not used");
        },
      },
      async measure() {
        throw new Error("not used");
      },
      async jobStatus() {
        return { status: opts.annotateStatus ?? "done", error: opts.annotateStatus ? "worker gone" : null };
      },
      trackJob: (o) => rec.tracked.push(o.label),
      wait: async () => {},
    },
  };
  return { deps, rec, annotated };
}

test("contact run, members missing: prompt about the ANNOTATE, request, re-plan, then annotate through the provider", async () => {
  const { memberKey } = await import("@/utils/groups/savedGroups");
  const rows = new Map([[memberKey(ELEMENT as never), "fn04"]]);
  const { deps, rec, annotated } = annotateFakes({ plans: [planOf(MISSING), planOf([])], rows });

  const out = await runGeometryAwareAnnotateFlow(deps, SCOPE, TARGET, RUN_KEY, PROVIDER);
  assert.ok(out);
  assert.deepEqual(rec.calls, ["plan", "prompt", "rows:1", "request", "refresh", "plan", `annotate:${PROVIDER}`, "read"]);
  assert.deepEqual(annotated, [PROVIDER]);
  assert.equal(rec.prompts[0].purpose, "annotate");
  assert.match(geometryPromptSentence(rec.prompts[0]), /before annotating the run/);
  assert.deepEqual(rec.pluginJobs[0].options.nodes, ["fn04", "fnorphan"]);
  assert.ok(rec.tracked.includes("Clash specs"));
  assert.ok(out.notes.some((n) => n.includes("Every member is now at member-reader")), out.notes.join(" | "));
  // The run read back after the annotate is the one put on the panel.
  assert.equal(out.run.provenance.geometry_provider, PROVIDER);
  assert.equal(out.runKey, RUN_KEY);
});

test("contact run, annotate without them: nothing requested, annotated through the provider anyway", async () => {
  const { deps, rec } = annotateFakes({ plans: [planOf(MISSING)], answer: "skip" });
  const out = await runGeometryAwareAnnotateFlow(deps, SCOPE, TARGET, RUN_KEY, PROVIDER);
  assert.ok(out);
  assert.deepEqual(rec.calls, ["plan", "prompt", `annotate:${PROVIDER}`, "read"]);
  assert.ok(out.notes.some((n) => n.includes("2 members not at member-reader left out")));
  const prompt = geometryRequestPrompt(planOf(MISSING), null, false, "annotate");
  assert.ok(prompt);
  assert.match(geometryPromptSentence(prompt), /The run would be annotated without them/);
});

test("contact run, cancel: no request and no annotate -- the run stays stored as it is", async () => {
  const { deps, rec } = annotateFakes({ plans: [planOf(MISSING)], answer: "cancel" });
  assert.equal(await runGeometryAwareAnnotateFlow(deps, SCOPE, TARGET, RUN_KEY, PROVIDER), null);
  assert.deepEqual(rec.calls, ["plan", "prompt"]);
});

test("contact run, nothing missing: no prompt, straight to the annotate; a failed annotate is a note", async () => {
  const ok = annotateFakes({ plans: [planOf([])] });
  const out = await runGeometryAwareAnnotateFlow(ok.deps, SCOPE, TARGET, RUN_KEY, PROVIDER);
  assert.ok(out);
  assert.deepEqual(ok.rec.calls, ["plan", `annotate:${PROVIDER}`, "read"]);
  assert.deepEqual(out.notes, []);

  const failed = annotateFakes({ plans: [planOf([])], annotateStatus: "error" });
  const after = await runGeometryAwareAnnotateFlow(failed.deps, SCOPE, TARGET, RUN_KEY, PROVIDER);
  assert.ok(after);
  assert.ok(after.notes.some((n) => n.includes("The specs were not resolved") && n.includes("worker gone")), after.notes.join(" | "));
});

test("contact run on a file: no plan, and annotated through each member's own reader", async () => {
  const { deps, rec, annotated } = annotateFakes({ plans: [] });
  const out = await runGeometryAwareAnnotateFlow(deps, SCOPE, { kind: "file", sourceKey: "a.ifc" }, RUN_KEY, PROVIDER);
  assert.ok(out);
  assert.deepEqual(rec.calls, ["annotate:own", "read"]);
  assert.deepEqual(annotated, [null]);
});

test("which provider a contact run is annotated through", () => {
  const readable = (id: string, ok = true) => ({ id, label: id, readable: ok }) as never;
  const avail = [readable(PROVIDER), readable("mesh-only", false)];
  // The members' own provider cannot be read: the readable one there is, without the user picking.
  assert.equal(contactGeometryProvider(TARGET, null, avail), PROVIDER);
  // The "Geometry from" choice wins; one that is every member's own is no re-addressing at all.
  assert.equal(contactGeometryProvider(TARGET, "other-reader", avail), "other-reader");
  assert.equal(contactGeometryProvider(TARGET, "mesh-only", avail), null);
  // The members' own provider IS readable: each member's own.
  assert.equal(contactGeometryProvider(TARGET, null, [readable(PROVIDER), readable("mesh-only")]), null);
  // Nothing known about the pools, nothing readable, or a file: each member's own.
  assert.equal(contactGeometryProvider(TARGET, null, null), null);
  assert.equal(contactGeometryProvider(TARGET, null, [readable("mesh-only", false)]), null);
  assert.equal(contactGeometryProvider({ kind: "file", sourceKey: "a.ifc" }, PROVIDER, avail), null);
  // A node: its own provider, not its collection's other publishers.
  const node = { kind: "node" as const, provider: "mesh-only", collection: "plant", subject: "fnzone", collectionProviders: [PROVIDER] };
  assert.equal(contactGeometryProvider(node as never, null, avail), PROVIDER);
});
