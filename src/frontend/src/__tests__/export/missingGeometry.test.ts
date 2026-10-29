// A selection export whose geometry provider has published nothing for the members yet: finding
// that outcome in the failed export's log (and not in a stale or older-server one), which providers
// the geometry can be requested from, the question, and the request built from exactly the members
// left out -- through the clash check's shared request (`requestUnmatchedMembers`).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { WireGeometryProvider } from "@/services/api/clashCheck";
import type { AssetNodeRequest } from "@/services/assetScopeCollections";
import {
  missingFromPlan,
  NO_PROVIDER_GEOMETRY,
  noGeometryQuestion,
  noProviderGeometryOf,
  requestProviderChoices,
  type DeclaredProvider,
} from "@/utils/export/missingGeometry";
import { requestUnmatchedMembers, unmatchedRequestParts } from "@/utils/groups/groupRequest";
import { memberKey, type GroupMember } from "@/utils/groups/savedGroups";

const TREE = "tree-only";
const SOLID = "solid-a";
const SENTENCE = "aaa has no solid-a geometry in this scope yet (2 members); request it from solid-a, then export again.";

const member = (element: string | null, subject = "fn-1"): GroupMember => ({
  target: { kind: "node", provider: TREE, collection: "aaa", subject, revision: "r1", node: subject },
  element,
  path: element ? [element] : [],
});
const PIPE = member("/PIPE-1");
const WHOLE = member(null, "fn-2");

const failedLog = (over: Record<string, unknown> = {}) => ({
  geometry_provider: SOLID,
  members: 2,
  warnings: [],
  error: {
    code: NO_PROVIDER_GEOMETRY,
    message: SENTENCE,
    geometry_provider: SOLID,
    collections: ["aaa"],
    members: 2,
    unmatched: [
      { member: PIPE, reason: "r1", label: "/PIPE-1", row: "row-7", cause: NO_PROVIDER_GEOMETRY },
      { member: WHOLE, reason: "r2", label: "/AREA-2", row: "fn-2", cause: NO_PROVIDER_GEOMETRY },
    ],
    ...over,
  },
});

const REQ: AssetNodeRequest = {
  pluginId: "geometry-fetch",
  options: { action: "fetch" },
  collectionOption: "collection",
  label: "Fetch geometry",
  requiresAdmin: false,
  nodeOption: "nodes",
  labelOption: "labels",
  maxNodes: 10,
};

// --- detecting the outcome ----------------------------------------------------------------------

test("the outcome is read from the failed export's log", () => {
  const out = noProviderGeometryOf(failedLog(), SENTENCE);
  assert.ok(out);
  assert.equal(out.provider, SOLID);
  assert.deepEqual(out.collections, ["aaa"]);
  assert.equal(out.members, 2);
  assert.equal(out.message, SENTENCE);
  assert.deepEqual(
    out.unmatched.map((u) => [u.label, u.row]),
    [["/PIPE-1", "row-7"], ["/AREA-2", "fn-2"]],
  );
});

test("an older server (no log on failure), another failure or another run's log is not the outcome", () => {
  assert.equal(noProviderGeometryOf(null, SENTENCE), null);
  // A successful export's log: no error at all.
  assert.equal(noProviderGeometryOf({ geometry_provider: SOLID, members: 2, warnings: [] }, "boom"), null);
  assert.equal(noProviderGeometryOf(failedLog({ code: "something_else" }), SENTENCE), null);
  assert.equal(noProviderGeometryOf(failedLog({ unmatched: [] }), SENTENCE), null);
  assert.equal(noProviderGeometryOf(failedLog({ collections: [] }), SENTENCE), null);
  // Left at the same key by an earlier run: this job failed for some other reason.
  assert.equal(noProviderGeometryOf(failedLog(), "none of the 2 member(s) could be resolved: ..."), null);
});

test("the question names the collection and the provider", () => {
  const out = noProviderGeometryOf(failedLog(), SENTENCE)!;
  assert.equal(noGeometryQuestion(out, "Solid A"), "No stored geometry for aaa from Solid A. Request it now?");
  const two = noProviderGeometryOf(failedLog({ collections: ["aaa", "bbb"] }), SENTENCE)!;
  assert.equal(noGeometryQuestion(two, SOLID), "No stored geometry for aaa and bbb from solid-a. Request it now?");
});

// --- whom to ask --------------------------------------------------------------------------------

const declared = (providerId: string, collections: string[], nodeRequest: AssetNodeRequest | null = REQ): DeclaredProvider => ({
  providerId,
  collections,
  nodeRequest,
});

const readable: WireGeometryProvider[] = [
  { id: SOLID, readable: true },
  { id: "solid-b", readable: true },
  { id: "solid-c", readable: false },
];

test("the named provider comes first; others must take node requests, be readable and offer the collection", () => {
  const outcome = noProviderGeometryOf(failedLog(), SENTENCE)!;
  const choices = requestProviderChoices({
    outcome,
    declared: [
      declared("solid-b", ["AAA", "bbb"]), // offered under the provider's own spelling
      declared(TREE, ["aaa"]), // the members' own provider: what the geometry is missing from
      declared("solid-c", ["aaa"]), // not readable here
      declared("solid-d", ["zzz"]), // does not offer the collection
      declared("solid-e", ["aaa"], null), // takes no node requests
      declared(SOLID, []),
    ],
    readable,
    isAdmin: false,
    providerName: (id) => id.toUpperCase(),
  });
  assert.deepEqual(
    choices.map((c) => [c.providerId, c.label]),
    [[SOLID, "SOLID-A"], ["solid-b", "SOLID-B"]],
  );
});

test("an admin-only request is offered to an administrator only", () => {
  const outcome = noProviderGeometryOf(failedLog(), SENTENCE)!;
  const adminOnly = { ...REQ, requiresAdmin: true };
  const args = { outcome, declared: [declared(SOLID, ["aaa"], adminOnly)], readable };
  assert.deepEqual(requestProviderChoices({ ...args, isAdmin: false }), []);
  assert.equal(requestProviderChoices({ ...args, isAdmin: true }).length, 1);
});

// --- the request -------------------------------------------------------------------------------

test("the request is built from the members left out, at the rows the server placed", () => {
  const outcome = noProviderGeometryOf(failedLog(), SENTENCE)!;
  const { members, labels, rows } = unmatchedRequestParts(outcome.unmatched);
  assert.deepEqual(members, [PIPE, WHOLE]);
  assert.equal(rows.get(memberKey(PIPE)), "row-7");
  // A whole node is its own id; its row is not needed.
  assert.equal(rows.has(memberKey(WHOLE)), false);
  assert.equal(labels.get(memberKey(WHOLE)), "/AREA-2");
});

test("Request asks the provider for exactly those nodes, with their labels, then publishes", async () => {
  const outcome = noProviderGeometryOf(failedLog(), SENTENCE)!;
  const jobs: { pluginId: string; options: Record<string, unknown> }[] = [];
  const calls: string[] = [];
  let n = 0;
  const { notes, outcome: out } = await requestUnmatchedMembers(
    {
      async resolveRows() {
        calls.push("rows");
        return new Map();
      },
      request: {
        api: {
          async pluginJob(pluginId, body) {
            jobs.push({ pluginId, options: body.options });
            n += 1;
            return { job_id: `fetch-${n}`, derived_key: `fetch-${n}.json` };
          },
          async publish(_scope, body) {
            calls.push(`publish:${body.provider}`);
            n += 1;
            return { job_id: `publish-${n}`, derived_key: `publish-${n}.json` };
          },
          async jobStatus() {
            return { status: "done", error: null };
          },
          async readJson(_scope, key) {
            return key.startsWith("fetch") ? { asset_staging_id: "stage-1" } : { collection: "aaa", revision: "r2" };
          },
        },
        wait: async () => {},
      },
      afterRequest: () => void calls.push("refresh"),
    },
    "user:me",
    SOLID,
    REQ,
    outcome.unmatched,
  );
  // Every row was placed by the server: no tree walk.
  assert.deepEqual(calls, [`publish:${SOLID}`, "refresh"]);
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].pluginId, "geometry-fetch");
  assert.equal(jobs[0].options.collection, "aaa");
  assert.deepEqual(jobs[0].options.nodes, ["row-7", "fn-2"]);
  assert.deepEqual(jobs[0].options.labels, ["/PIPE-1", "/AREA-2"]);
  assert.ok(out && out.published === 1 && out.failed.length === 0);
  assert.ok(notes.some((t) => t.startsWith(`Requested from ${SOLID}`)), notes.join(" | "));
});

test("a plan the provider has part of is asked about before exporting, and one it has all of is not", () => {
  const plan = (unmatched: GroupMember[], matched: number) => ({
    geometry_provider: SOLID,
    matched: Array.from({ length: matched }, () => ({ from: PIPE, to: PIPE })),
    unchanged: [],
    unmatched: unmatched.map((m) => ({ member: m, reason: "no node of that name", label: m.element, row: null })),
  });
  const outcome = missingFromPlan(plan([PIPE, WHOLE], 7));
  assert.ok(outcome);
  assert.equal(outcome.provider, SOLID);
  assert.deepEqual(outcome.collections, ["aaa"]);
  assert.equal(outcome.members, 9);
  assert.equal(outcome.present, 7);
  assert.equal(outcome.unmatched.length, 2);
  assert.equal(
    noGeometryQuestion(outcome, "Solid A"),
    "2 of 9 members have no stored geometry from Solid A. Request them before exporting?",
  );
  // Everything is there: nothing to ask.
  assert.equal(missingFromPlan(plan([], 9)), null);
});
