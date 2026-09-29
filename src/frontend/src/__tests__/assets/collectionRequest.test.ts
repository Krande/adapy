import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  CollectionRequestError,
  requestCollection,
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
