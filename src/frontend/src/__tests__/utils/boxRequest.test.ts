// "Request from source" on a selection box: which sources may be asked (design-geometry providers
// that take node requests -- never a mesh-only one), what a geometry plan of the box's members says,
// and the request made from exactly the plan's unmatched members.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { WireGeometryPlan, WireGeometryProvider } from "@/services/api/clashCheck";
import type { AssetNodeRequest } from "@/services/assetScopeCollections";
import {
  boxPlanTarget,
  boxRequestChoices,
  boxRequestSentence,
  noChoiceReason,
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

const declared: DeclaredProvider[] = [
  { providerId: MESH, collections: ["aaa"], nodeRequest: req("mesh-fetch", { onDemand: true }) },
  { providerId: SOLID_B, collections: ["aaa", "bbb"], nodeRequest: req("solid-b-fetch") },
  { providerId: SOLID, collections: [], nodeRequest: req("solid-fetch") },
  { providerId: "no-requests", collections: [], nodeRequest: null },
];
const readable: WireGeometryProvider[] = [
  { id: SOLID, readable: true },
  { id: SOLID_B, readable: true },
  { id: "no-requests", readable: true },
];

test("only design-geometry providers that take node requests are offered, the last one asked first", () => {
  const ids = (preferred: string | null = null) =>
    boxRequestChoices({ members: [member("PIPE-1")], declared, readable, isAdmin: false, preferred }).map((c) => c.providerId);
  // The mesh-only provider takes requests but reads into nothing: never offered.
  assert.deepEqual(ids(), [SOLID, SOLID_B]);
  assert.deepEqual(ids(SOLID_B), [SOLID_B, SOLID]);
});

test("a provider that does not offer every collection in the box, or is admin-only, is left out", () => {
  const members = [member("PIPE-1"), member("BEAM-2", "ccc")];
  assert.deepEqual(
    boxRequestChoices({ members, declared, readable, isAdmin: false }).map((c) => c.providerId),
    [SOLID],
  );
  const adminOnly: DeclaredProvider[] = [{ providerId: SOLID, collections: [], nodeRequest: req("x", { requiresAdmin: true }) }];
  assert.equal(boxRequestChoices({ members, declared: adminOnly, readable, isAdmin: false }).length, 0);
  assert.equal(boxRequestChoices({ members, declared: adminOnly, readable, isAdmin: true }).length, 1);
});

test("without the readable list nothing is offered, and the reason says why", () => {
  assert.equal(boxRequestChoices({ members: [member("PIPE-1")], declared, readable: null, isAdmin: true }).length, 0);
  assert.match(noChoiceReason([member("PIPE-1")], null), /Could not list/);
  assert.match(noChoiceReason([FILE], readable), /from a file/);
  assert.match(noChoiceReason([member("PIPE-1")], readable), /No design-geometry source/);
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
  assert.equal(
    boxRequestSentence(s, "Solid A", 12),
    "12 elements in the box (5 members); 2 already at Solid A, 1 from a file, which cannot be requested. Request 2 members from Solid A?",
  );
  assert.equal(
    boxRequestSentence({ provider: SOLID, missing: 0, present: 1, files: 0 }, "Solid A", 1),
    "1 element in the box (1 member); 1 already at Solid A. Nothing to request.",
  );
  assert.deepEqual(boxPlanTarget("Selection box 1", [PIPE]), { kind: "group", name: "Selection box 1", members: [PIPE] });
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
