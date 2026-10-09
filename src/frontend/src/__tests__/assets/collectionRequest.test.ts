import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  CollectionRequestError,
  nodeBatches,
  nodeRequestOptions,
  nodesRequestOptions,
  requestCollection,
  requestNode,
  requestNodes,
  requestOptionChoices,
  unchangedOf,
  nothingOf,
  requestOptions,
  stagingIdOf,
  type CollectionRequestApi,
} from "../../assets/collectionRequest";
import type { AssetCollectionRequest } from "../../services/assetScopeCollections";

const REQ: AssetCollectionRequest = {
  pluginId: "exporter",
  options: { action: "fetch-tree", elements: [] },
  collectionOption: "project",
  label: "Request tree",
  requiresAdmin: false,
};

/** A fake api: jobs finish after `ticks` polls, summaries come from `blobs`. */
function fakeApi(opts: { fetchSummary?: unknown; fetchStatus?: string; ticks?: number } = {}) {
  const calls: { kind: string; args: unknown[] }[] = [];
  const polls = new Map<string, number>();
  const blobs: Record<string, unknown> = {
    "fetch.json": opts.fetchSummary ?? { asset_staging_id: "abc123" },
    "publish.json": { collection: "alpha", revision: "r1", subjects: ["n1", "n2"], written: [], dry_run: false },
  };
  const api: CollectionRequestApi = {
    async pluginJob(pluginId, body, o) {
      calls.push({ kind: "pluginJob", args: [pluginId, body, o] });
      return { job_id: "j-fetch", derived_key: "fetch.json" };
    },
    async publish(scope, body) {
      calls.push({ kind: "publish", args: [scope, body] });
      return { job_id: "j-publish", derived_key: "publish.json" };
    },
    async jobStatus(jobId) {
      const n = (polls.get(jobId) ?? 0) + 1;
      polls.set(jobId, n);
      if (jobId === "j-fetch" && opts.fetchStatus) return { status: opts.fetchStatus, error: "boom" };
      return { status: n > (opts.ticks ?? 1) ? "done" : "running", stage: "working", error: null };
    },
    async readJson(_scope, key) {
      return blobs[key];
    },
  };
  return { api, calls };
}

const noWait = async () => {};

test("requestOptions adds the collection under the declared name and a stamp, nothing else", () => {
  assert.deepEqual(requestOptions(REQ, "ALPHA", "t0"), {
    action: "fetch-tree",
    elements: [],
    project: "ALPHA",
    requested_at: "t0",
  });
});

test("stagingIdOf refuses a summary without one, by name", () => {
  assert.equal(stagingIdOf({ asset_staging_id: " x1 " }), "x1");
  for (const bad of [null, {}, { asset_staging_id: "" }, { asset_staging_id: 3 }]) {
    assert.throws(() => stagingIdOf(bad), CollectionRequestError);
  }
});

test("a request fetches, then publishes the staged id under the provider's id", async () => {
  const { api, calls } = fakeApi({ ticks: 2 });
  const tracked: string[] = [];
  const out = await requestCollection(
    { api, wait: noWait, trackJob: (o) => tracked.push(o.label) },
    "project:1",
    "vendor",
    REQ,
    "ALPHA",
  );
  assert.equal(calls[0].kind, "pluginJob");
  assert.equal(calls[0].args[0], "exporter");
  assert.deepEqual(calls[0].args[2], { scope: "project:1" });
  assert.deepEqual(calls[1], { kind: "publish", args: ["project:1", { provider: "vendor", staging_id: "abc123" }] });
  assert.deepEqual(tracked, ["Request tree: ALPHA", "Publish ALPHA"]);
  assert.deepEqual(out, { stagingId: "abc123", collection: "alpha", revision: "r1", subjects: ["n1", "n2"] });
});

test("a failed fetch stops before anything is published", async () => {
  const { api, calls } = fakeApi({ fetchStatus: "error" });
  await assert.rejects(
    requestCollection({ api, wait: noWait }, "shared", "vendor", REQ, "ALPHA"),
    /the request for ALPHA error: boom/,
  );
  assert.ok(!calls.some((c) => c.kind === "publish"));
});

test("a fetch that stages nothing says so instead of publishing", async () => {
  const { api, calls } = fakeApi({ fetchSummary: { status: "ok" } });
  await assert.rejects(
    requestCollection({ api, wait: noWait }, "shared", "vendor", REQ, "ALPHA"),
    /asset_staging_id/,
  );
  assert.ok(!calls.some((c) => c.kind === "publish"));
});

test("a request that outlives its timeout gives up without cancelling, and says where to look", async () => {
  const { api } = fakeApi({ ticks: 1e9 });
  let t = 0;
  await assert.rejects(
    requestCollection({ api, wait: noWait, now: () => (t += 60_000) }, "shared", "vendor", REQ, "ALPHA"),
    /not cancelled.*Staged, not published/,
  );
});

test("a node request sends the collection request's options plus the node, as a list", () => {
  const nodeReq = { ...REQ, nodeOption: "nodes" };
  assert.deepEqual(nodeRequestOptions(nodeReq, "ALPHA", "node-7", "t0"), {
    action: "fetch-tree",
    elements: [],
    project: "ALPHA",
    requested_at: "t0",
    nodes: ["node-7"],
  });
});

test("a node request stages and publishes like a collection request", async () => {
  const { api, calls } = fakeApi();
  const tracked: string[] = [];
  const out = await requestNode(
    { api, wait: noWait, trackJob: (o) => tracked.push(o.label) },
    "project:1",
    "vendor",
    { ...REQ, nodeOption: "nodes", label: "Request geometry" },
    "alpha",
    "n1",
    "Node one",
  );
  assert.equal(out.stagingId, "abc123");
  const [job, publish] = calls.filter((c) => c.kind !== "readJson");
  assert.deepEqual((job.args[1] as { options: Record<string, unknown> }).options.nodes, ["n1"]);
  assert.deepEqual(publish.args[1], { provider: "vendor", staging_id: "abc123" });
  assert.deepEqual(tracked, ["Request geometry: alpha / Node one", "Publish alpha / Node one"]);
});

test("a node request carries the node's label only when the provider declares where it goes", () => {
  const plain = nodeRequestOptions({ ...REQ, nodeOption: "nodes" }, "ALPHA", "n1", "t0", "/SITE-1");
  assert.equal("labels" in plain, false);
  const labelled = nodeRequestOptions({ ...REQ, nodeOption: "nodes", labelOption: "labels" }, "ALPHA", "n1", "t0", "/SITE-1");
  assert.deepEqual(labelled.labels, ["/SITE-1"]);
  assert.deepEqual(labelled.nodes, ["n1"]);
});
test("several nodes go in one request: ids in order, labels index for index", () => {
  const req = { ...REQ, nodeOption: "nodes", labelOption: "labels" };
  const options = nodesRequestOptions(req, "ALPHA", [{ id: "n1", label: "/SITE-1" }, { id: "n2" }, { id: "n3", label: "/SITE-3" }], "t0");
  assert.deepEqual(options.nodes, ["n1", "n2", "n3"]);
  assert.deepEqual(options.labels, ["/SITE-1", "", "/SITE-3"], "a node without a label keeps its slot");
  assert.equal("labels" in nodesRequestOptions(req, "ALPHA", [{ id: "n1" }], "t0"), false, "no labels at all: none sent");
});

test("a selection is cut into batches of the provider's max_nodes, a node each when undeclared", () => {
  const ids = ["a", "b", "c", "d", "e"];
  assert.deepEqual(nodeBatches({ ...REQ, nodeOption: "nodes" }, ids), [["a"], ["b"], ["c"], ["d"], ["e"]]);
  assert.deepEqual(nodeBatches({ ...REQ, nodeOption: "nodes", maxNodes: 2 }, ids), [["a", "b"], ["c", "d"], ["e"]]);
  assert.deepEqual(nodeBatches({ ...REQ, nodeOption: "nodes", maxNodes: 20 }, ids), [ids]);
});

test("a batch is one job naming every node in it", async () => {
  const { api, calls } = fakeApi();
  const tracked: string[] = [];
  await requestNodes(
    { api, wait: noWait, trackJob: (o) => tracked.push(o.label) },
    "project:1",
    "vendor",
    { ...REQ, nodeOption: "nodes", label: "Request geometry" },
    "alpha",
    [{ id: "n1" }, { id: "n2" }],
  );
  const jobs = calls.filter((c) => c.kind === "pluginJob");
  assert.equal(jobs.length, 1);
  assert.deepEqual((jobs[0].args[1] as { options: Record<string, unknown> }).options.nodes, ["n1", "n2"]);
  assert.deepEqual(tracked, ["Request geometry: alpha / 2 nodes", "Publish alpha / 2 nodes"]);
});

test("a provider that finds its source unchanged gets no publish, and the outcome says so", async () => {
  const { api, calls } = fakeApi({ fetchSummary: { asset_unchanged: true, revision: "r0", subjects: ["n1"], message: "same ETags" } });
  const out = await requestNode({ api, wait: noWait }, "project:1", "vendor", { ...REQ, nodeOption: "nodes" }, "alpha", "n1");
  assert.equal(out.unchanged, true);
  assert.equal(out.revision, "r0");
  assert.deepEqual(out.subjects, ["n1"]);
  assert.equal(out.message, "same ETags");
  assert.equal(calls.filter((c) => c.kind === "publish").length, 0, "nothing is published");
  assert.equal(unchangedOf({ asset_staging_id: "x" }), null);
});

test("a provider with nothing for the node answers without a publish and without failing", async () => {
  const { api, calls } = fakeApi({ fetchSummary: { asset_nothing: true, message: "no such site in the current export" } });
  const out = await requestNode({ api, wait: noWait }, "project:1", "vendor", { ...REQ, nodeOption: "nodes" }, "alpha", "n1");
  assert.equal(out.nothing, true);
  assert.equal(out.message, "no such site in the current export");
  assert.equal(out.stagingId, "");
  assert.equal(calls.filter((c) => c.kind === "publish").length, 0, "nothing is published");
  assert.equal(nothingOf({ asset_staging_id: "x" }), null);
});
// --- provider options: sent with every request for the collection ---------------------------

test("a collection's provider options are sent under the declared options, never over them", async () => {
  const { api, calls } = fakeApi();
  const asked: unknown[] = [];
  await requestCollection(
    {
      api,
      wait: noWait,
      providerOptions: async (scope, provider, collection) => {
        asked.push([scope, provider, collection]);
        // `action` and `project` here must lose to the request's own.
        return { extra_dbs: ["X"], action: "something-else", project: "OTHER" };
      },
    },
    "project:1",
    "vendor",
    REQ,
    "ALPHA",
  );
  assert.deepEqual(asked, [["project:1", "vendor", "ALPHA"]]);
  const sent = (calls[0].args[1] as { options: Record<string, unknown> }).options;
  assert.deepEqual(sent.extra_dbs, ["X"]);
  assert.equal(sent.action, "fetch-tree");
  assert.equal(sent.project, "ALPHA");
});

test("node requests carry the provider options too", async () => {
  const { api, calls } = fakeApi();
  const nodeReq = { ...REQ, nodeOption: "nodes" };
  await requestNodes({ api, wait: noWait, providerOptions: async () => ({ extra_dbs: ["X"] }) }, "s", "vendor", nodeReq, "ALPHA", [
    { id: "n1" },
  ]);
  const sent = (calls[0].args[1] as { options: Record<string, unknown> }).options;
  assert.deepEqual(sent.extra_dbs, ["X"]);
  assert.deepEqual(sent.nodes, ["n1"]);
});

test("options that cannot be read fail the request before anything is asked", async () => {
  const { api, calls } = fakeApi();
  await assert.rejects(
    requestCollection(
      {
        api,
        wait: noWait,
        providerOptions: async () => {
          throw new Error("unreadable");
        },
      },
      "s",
      "vendor",
      REQ,
      "ALPHA",
    ),
    (e: unknown) => e instanceof CollectionRequestError && /unreadable/.test(e.message) && /was not requested/.test(e.message),
  );
  assert.equal(calls.length, 0);
});

test("a choices request runs the job and hands back its summary, publishing nothing", async () => {
  const { api, calls } = fakeApi({ fetchSummary: { option_choices: { extra_dbs: [{ value: "X" }] } } });
  const summary = await requestOptionChoices({ api, wait: noWait }, "s", { ...REQ, options: { action: "list-choices" } }, "ALPHA");
  assert.deepEqual(summary, { option_choices: { extra_dbs: [{ value: "X" }] } });
  assert.deepEqual(calls.map((c) => c.kind), ["pluginJob"]);
  const sent = (calls[0].args[1] as { options: Record<string, unknown> }).options;
  assert.equal(sent.action, "list-choices");
  assert.equal(sent.project, "ALPHA");
});