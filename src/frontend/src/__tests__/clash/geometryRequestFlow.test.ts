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

const { runGeometryAwareCheckFlow, geometryRequestPrompt, geometryPromptSentence } = await import(
  "@/state/clashCheckStore"
);
type Deps = Parameters<typeof runGeometryAwareCheckFlow>[0];
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

function planOf(unmatched: readonly { member: unknown; reason: string; label?: string | null }[]): Plan {
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
  assert.deepEqual(rec.calls, ["plan", "prompt", "rows:2", "request", "refresh", "plan", "check"]);

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
