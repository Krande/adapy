// "Request from source" on a selection box: which sources may be asked (every provider that takes
// node requests for the box's collections, mesh providers included), which one is the default (the
// last explicit pick while it qualifies, else the members' own provider, else the first in the
// Sources order), what a geometry plan of the box's members says, and the request made from exactly
// the plan's unmatched members. And the published-bounds path: the scene box moved into the model
// frame, one bounds set per collection, the hits as whole-node members.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { WireGeometryPlan } from "@/services/api/clashCheck";
import type { BoundsHit, BoundsSet } from "@/services/api/nodeBounds";
import type { AssetNodeRequest } from "@/services/assetScopeCollections";
import {
  boundsMembers,
  boundsMode,
  boxPlanTarget,
  boxRequestChoices,
  boxRequestSentence,
  defaultBoxProvider,
  memberProviders,
  noChoiceReason,
  pickBoundsSets,
  presentMembers,
  sceneBoxToSource,
  summariseBoxPlan,
} from "@/utils/bboxSelection/boxRequest";
import type { DeclaredProvider } from "@/utils/export/missingGeometry";
import { requestUnmatchedMembers } from "@/utils/groups/groupRequest";
import type { GroupMember } from "@/utils/groups/savedGroups";

const MESH = "mesh-only";
const SOLID = "solid-a";
const SOLID_B = "solid-b";

const member = (element: string | null, collection = "aaa", provider = MESH): GroupMember => ({
  target: { kind: "node", provider, collection, subject: "site-1", revision: "r1", node: "site-1" },
  element,
  path: element ? ["ZONE-1", element] : [],
});
const FILE: GroupMember = { target: { kind: "file", source_key: "models/a.ifc" }, element: "beam", path: ["beam"] };

const req = (pluginId: string, over: Partial<AssetNodeRequest> = {}): AssetNodeRequest => ({
  pluginId,
  options: { action: "fetch" },
  collectionOption: "collection",
  label: "Fetch",
  requiresAdmin: false,
  nodeOption: "nodes",
  labelOption: "labels",
  maxNodes: 50,
  ...over,
});

// In the Sources order: by provider id.
const declared: DeclaredProvider[] = [
  { providerId: MESH, collections: ["aaa"], nodeRequest: req("mesh-fetch", { onDemand: true }) },
  { providerId: "no-requests", collections: [], nodeRequest: null },
  { providerId: SOLID, collections: [], nodeRequest: req("solid-fetch") },
  { providerId: SOLID_B, collections: ["aaa", "bbb"], nodeRequest: req("solid-b-fetch") },
];

const ids = (members: readonly GroupMember[], isAdmin = false, from = declared) =>
  boxRequestChoices({ members, declared: from, isAdmin }).map((c) => c.providerId);

test("every provider that takes node requests for the collection is offered, mesh providers included", () => {
  assert.deepEqual(ids([member("PIPE-1", "aaa", "other")]), [MESH, SOLID, SOLID_B]);
});

test("the members' own provider comes first, then the Sources order", () => {
  assert.deepEqual(ids([member("PIPE-1", "aaa", SOLID_B)]), [SOLID_B, MESH, SOLID]);
  // Members from two providers: the one most of them came from first.
  const mixed = [member("A", "aaa", SOLID), member("B", "aaa", SOLID_B), member("C", "aaa", SOLID_B)];
  assert.deepEqual(memberProviders(mixed), [SOLID_B, SOLID]);
  assert.deepEqual(ids(mixed), [SOLID_B, SOLID, MESH]);
});

test("a provider that does not cover every collection in the box, or is admin-only, is left out", () => {
  const members = [member("PIPE-1"), member("BEAM-2", "ccc")];
  assert.deepEqual(ids(members), [SOLID]);
  const adminOnly: DeclaredProvider[] = [{ providerId: SOLID, collections: [], nodeRequest: req("x", { requiresAdmin: true }) }];
  assert.deepEqual(ids(members, false, adminOnly), []);
  assert.deepEqual(ids(members, true, adminOnly), [SOLID]);
  // Files name no collection: nothing to ask anyone for.
  assert.deepEqual(ids([FILE]), []);
});

test("the default is the last explicit pick while it qualifies, else the first available provider", () => {
  const choices = boxRequestChoices({ members: [member("PIPE-1")], declared, isAdmin: false });
  assert.equal(defaultBoxProvider(choices, null), MESH); // the members' own source
  assert.equal(defaultBoxProvider(choices, SOLID_B), SOLID_B);
  assert.equal(defaultBoxProvider(choices, "gone-offline"), MESH);
  assert.equal(defaultBoxProvider([], SOLID), null);
});

test("the reason when nothing can be asked", () => {
  assert.match(noChoiceReason([FILE]), /from a file/);
  assert.match(noChoiceReason([member("PIPE-1")]), /No source that takes requests/);
});

const PIPE = member("PIPE-1");
const BEAM = member("BEAM-2");
const OWN = member("PLATE-3", "aaa", SOLID);
const plan: WireGeometryPlan = {
  geometry_provider: SOLID,
  matched: [{ from: BEAM, to: member("BEAM-2", "aaa", SOLID) }],
  unmatched: [
    { member: PIPE, reason: "not published", label: "/PIPE-1", row: "row-7" },
    { member: member("VALVE-4"), reason: "not published", label: "/VALVE-4", row: "row-9" },
  ],
  unchanged: [OWN, FILE],
};

test("a plan's summary: what is missing, what the source already has, and what is in a file", () => {
  const s = summariseBoxPlan(plan);
  assert.deepEqual(s, { provider: SOLID, missing: 2, present: 2, files: 1 });
  const drawn = (elements: number, why: string | null = null) => ({ kind: "drawn" as const, elements, why });
  assert.equal(
    boxRequestSentence(s, "Solid A", drawn(12)),
    "12 drawn elements in the box (5 members); 2 already at Solid A, 1 from a file, which cannot be requested. Request 2 members from Solid A?",
  );
  assert.equal(
    boxRequestSentence({ provider: SOLID, missing: 0, present: 1, files: 0 }, "Solid A", drawn(1, "no source published node bounds for aaa")),
    "1 drawn element in the box (1 member) -- no source published node bounds for aaa, so only what is drawn was looked at; 1 already at Solid A. Nothing to request.",
  );
  assert.equal(
    boxRequestSentence(
      { provider: SOLID, missing: 3, present: 4, files: 0 },
      "Solid A",
      { kind: "bounds", providers: [MESH], truncated: null },
    ),
    `7 nodes in the box (by ${MESH}'s published bounds, loaded or not); 4 already at Solid A. Request 3 members from Solid A?`,
  );
  assert.deepEqual(boxPlanTarget("Selection box 1", [PIPE]), { kind: "group", name: "Selection box 1", members: [PIPE] });
});

test("what the source already has: matched members as re-addressed, its own nodes, never a file", () => {
  assert.deepEqual(presentMembers(plan), [member("BEAM-2", "aaa", SOLID), OWN]);
});

// --- the published bounds ---------------------------------------------------------------------------

const set = (provider: string, collection = "aaa", scope = "shared"): BoundsSet => ({
  scope,
  provider,
  collection,
  revision: "r1",
  rows: 10,
  frame: { up: "z" },
  ingested_at: null,
});
const hit = (node: string, provider = MESH, collection = "aaa", subject: string | null = "site-1"): BoundsHit => ({
  scope: "shared",
  provider,
  collection,
  node_id: node,
  parent_id: null,
  subject: subject as string,
  label: node.toUpperCase(),
  full_name: `/${node}`,
  kind: "zone",
  depth: 2,
  bbox: [0, 0, 0, 1, 1, 1],
});

test("the scene box moves into the model frame by the scene's offset", () => {
  assert.deepEqual(sceneBoxToSource({ min: [1, 2, 3], max: [4, 5, 6] }, [-100, 10, 0.5]), {
    min: [101, -8, 2.5],
    max: [104, -5, 5.5],
  });
  assert.deepEqual(sceneBoxToSource({ min: [1, 2, 3], max: [4, 5, 6] }, null), { min: [1, 2, 3], max: [4, 5, 6] });
  assert.equal(boundsMode("full"), "within");
  assert.equal(boundsMode("partial"), "partial");
});

test("one bounds set per collection: the scene's provider first, else the first by id", () => {
  const sets = [set(SOLID_B), set(MESH), set(SOLID, "bbb")];
  const picked = pickBoundsSets(sets, [SOLID_B]);
  assert.equal(picked.get("aaa")?.provider, SOLID_B);
  assert.equal(picked.get("bbb")?.provider, SOLID);
  assert.equal(pickBoundsSets(sets, []).get("aaa")?.provider, MESH);
});

test("hits become whole-node members through their subject; another set's hits and repeats are dropped", () => {
  const picked = pickBoundsSets([set(MESH), set(SOLID)], [MESH]);
  const members = boundsMembers([hit("zone-1"), hit("zone-1"), hit("zone-2", SOLID), hit("orphan", MESH, "aaa", null)], picked);
  assert.deepEqual(members, [
    {
      target: { kind: "node", provider: MESH, collection: "aaa", subject: "site-1", revision: null, node: "zone-1" },
      element: null,
      path: [],
    },
  ]);
});

test("with members from another provider's bounds, the scene's provider is still asked first", () => {
  const members = [member(null, "aaa", MESH)];
  const choices = boxRequestChoices({ members, declared, isAdmin: false, rankFirst: [SOLID_B] });
  assert.equal(defaultBoxProvider(choices, null), SOLID_B);
});

test("the request asks for exactly the plan's unmatched members, by row and label, in one batch", async () => {
  const jobs: { pluginId: string; options: Record<string, unknown> }[] = [];
  const { notes, outcome } = await requestUnmatchedMembers(
    {
      resolveRows: async () => {
        throw new Error("every member was placed by the plan");
      },
      request: {
        api: {
          async pluginJob(pluginId, body) {
            jobs.push({ pluginId, options: body.options });
            return { job_id: `job-${jobs.length}`, derived_key: `k-${jobs.length}` };
          },
          async publish() {
            return { job_id: "pub", derived_key: "pub-k" };
          },
          async jobStatus() {
            return { status: "done", error: null };
          },
          async readJson(_scope, key) {
            return key === "pub-k" ? { collection: "aaa", revision: "r2", subjects: ["site-1"] } : { asset_staging_id: "st-1" };
          },
        },
        wait: async () => {},
      },
    },
    "shared",
    SOLID,
    req("solid-fetch"),
    plan.unmatched,
  );
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].pluginId, "solid-fetch");
  assert.deepEqual(jobs[0].options.nodes, ["row-7", "row-9"]);
  assert.deepEqual(jobs[0].options.labels, ["PIPE-1", "VALVE-4"]);
  assert.equal(jobs[0].options.collection, "aaa");
  assert.equal(outcome?.published, 1);
  assert.ok(notes.some((n) => n.includes("1 request published")));
});
