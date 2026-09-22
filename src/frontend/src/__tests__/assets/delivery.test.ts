// `./delivery` is the only module that turns a claim into scene content, so
// these tests drive it directly against fakes -- no network, no three.js.
//
// Pins:
//   - a `mesh` claim loads through the scene loader with the right source
//     name / up-axis / headers, and a storage-key url resolves through the
//     blob route while an already-absolute url does not.
//   - a `build` claim answered `cached: true` reads the summary WITHOUT
//     enqueueing (no `jobStatus` call).
//   - a `build` claim answered with a job polls `jobStatus` to `done`, then
//     reads the summary.
//   - a summary whose `provenance.revision` (and separately `fingerprint`,
//     and separately a `glb_key` outside the derived prefix) disagrees is
//     REFUSED, the disagreeing field named, and nothing is loaded.
//   - a second load of an already-loaded source name does not re-fetch.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  assetSourceName,
  DeliveryError,
  loadNode,
  parseBuildSummary,
  parseDeliveryClaim,
  validateBuildSummary,
  type DeliveryApi,
  type LoadNodeDeps,
  type NodeRef,
} from "../../assets/delivery";
import type { WireBuildAssetResponse, WireDeliveryClaim } from "../../assets/types";

const SCOPE = "user:me";
const REF: NodeRef = {
  provider: "fixture-lines",
  collection: "plant-a",
  subject: "area-1",
  revision: "20260901T100000Z",
};

function noWait(_ms: number): Promise<void> {
  return Promise.resolve();
}

/** A `DeliveryApi` + scene-loader double whose call log is inspectable. Every
 *  method is overridable per test via the `over` bag. */
function fakeDeps(over: Partial<DeliveryApi> = {}): {
  deps: LoadNodeDeps;
  calls: { loadModelFromUrl: [string, string, unknown][]; buildAssetNode: unknown[]; getBuildSummary: string[]; jobStatus: string[] };
  loadedNames: Set<string>;
} {
  const calls = {
    loadModelFromUrl: [] as [string, string, unknown][],
    buildAssetNode: [] as unknown[],
    getBuildSummary: [] as string[],
    jobStatus: [] as string[],
  };
  const loadedNames = new Set<string>();
  const api: DeliveryApi = {
    async buildAssetNode(_scope, body) {
      calls.buildAssetNode.push(body);
      throw new Error("buildAssetNode not stubbed");
    },
    async getBuildSummary(_scope, key) {
      calls.getBuildSummary.push(key);
      throw new Error("getBuildSummary not stubbed");
    },
    async jobStatus(jobId) {
      calls.jobStatus.push(jobId);
      throw new Error("jobStatus not stubbed");
    },
    ...over,
  };
  const deps: LoadNodeDeps = {
    api,
    async loadModelFromUrl(owner, url, opts) {
      calls.loadModelFromUrl.push([owner, url, opts]);
      loadedNames.add((opts as { sourceName?: string } | undefined)?.sourceName ?? "");
    },
    isLoaded: (name) => loadedNames.has(name),
    blobUrl: (scope, key) => `blob://${scope}/${key}`,
    wait: noWait,
  };
  return { deps, calls, loadedNames };
}

function buildResponse(over: Partial<WireBuildAssetResponse> = {}): WireBuildAssetResponse {
  return {
    derived_key: "_derived/assets/fixture-lines/plant-a/area-1/20260901T100000Z/all/fp123/summary.json",
    capability: "asset-build-fixture",
    provider: "fixture-lines",
    subject: "area-1",
    revision: "20260901T100000Z",
    node: "area-1",
    fingerprint: "fp123",
    job_id: null,
    cached: true,
    ...over,
  };
}

function summaryDoc(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: "ada.assets/build@1",
    ok: true,
    glb_key: "_derived/assets/fixture-lines/plant-a/area-1/20260901T100000Z/all/fp123/model.glb",
    provenance: {
      provider: "fixture-lines",
      collection: "plant-a",
      subject: "area-1",
      revision: "20260901T100000Z",
      node: "area-1",
      fingerprint: "fp123",
      built_at: "2026-09-01T10:05:00Z",
    },
    counts: { drawn_members: 3 },
    ...over,
  };
}

// --- assetSourceName ---------------------------------------------------------------

test("assetSourceName omits #node when the row IS the subject", () => {
  assert.equal(assetSourceName(REF), "assets:fixture-lines/plant-a/area-1@20260901T100000Z");
});

test("assetSourceName appends #node for a covered (ghost) row", () => {
  const name = assetSourceName({ ...REF, node: "member-3" });
  assert.equal(name, "assets:fixture-lines/plant-a/area-1@20260901T100000Z#member-3");
});

// --- mesh ----------------------------------------------------------------------

test("a mesh claim loads through the scene loader with source name / up-axis / headers", async () => {
  const { deps, calls } = fakeDeps();
  const claim: WireDeliveryClaim = {
    kind: "mesh",
    url: "https://cdn.example/models/area-1.glb?sig=abc",
    headers: { Authorization: "Bearer tok" },
    source_up_axis: "y",
    revision: "20260901T100000Z",
    provider: "fixture-lines",
  };
  const asset = await loadNode(deps, SCOPE, REF, parseDeliveryClaim(claim));
  assert.equal(calls.loadModelFromUrl.length, 1);
  const [owner, url, opts] = calls.loadModelFromUrl[0];
  assert.equal(owner, "assets");
  assert.equal(url, "https://cdn.example/models/area-1.glb?sig=abc");
  assert.deepEqual(opts, {
    sourceName: "assets:fixture-lines/plant-a/area-1@20260901T100000Z",
    headers: { Authorization: "Bearer tok" },
    sourceUpAxis: "y",
  });
  assert.equal(asset.sourceName, "assets:fixture-lines/plant-a/area-1@20260901T100000Z");
  assert.equal(asset.glbKey, undefined);
});

test("a mesh claim whose url is a storage KEY (the published provider) resolves through the blob route", async () => {
  const { deps, calls } = fakeDeps();
  const claim: WireDeliveryClaim = {
    kind: "mesh",
    url: "assets/plant-a/area-1/20260901T100000Z/mesh.glb",
    source_up_axis: "z",
    revision: "20260901T100000Z",
    provider: "published",
  };
  await loadNode(deps, SCOPE, REF, parseDeliveryClaim(claim));
  const [, url] = calls.loadModelFromUrl[0];
  assert.equal(url, `blob://${SCOPE}/assets/plant-a/area-1/20260901T100000Z/mesh.glb`);
});

// --- build: cached ---------------------------------------------------------------

test("a cached build reads the summary WITHOUT enqueueing (no jobStatus call)", async () => {
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse();
    },
    async getBuildSummary(_scope, key) {
      calls.getBuildSummary.push(key);
      return summaryDoc();
    },
  });
  const asset = await loadNode(deps, SCOPE, REF, {
    kind: "build",
    capability: "asset-build-fixture",
    options: {},
    fingerprintInputs: [],
    revision: "20260901T100000Z",
    provider: "fixture-lines",
  });
  assert.equal(calls.jobStatus.length, 0, "cached must not poll a job");
  assert.equal(calls.getBuildSummary.length, 1);
  assert.equal(calls.getBuildSummary[0], buildResponse().derived_key);
  assert.equal(calls.loadModelFromUrl.length, 1);
  const [, url] = calls.loadModelFromUrl[0];
  assert.equal(url, `blob://${SCOPE}/${summaryDoc().glb_key}`);
  assert.equal(asset.glbKey, summaryDoc().glb_key);
  assert.deepEqual(asset.counts, { drawn_members: 3 });
});

// --- build: job --------------------------------------------------------------------

test("an uncached build polls the job to done, then reads the summary and loads it", async () => {
  const statuses = ["queued", "running", "done"];
  let tick = 0;
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse({ job_id: "job-1", cached: false });
    },
    async jobStatus(jobId) {
      calls.jobStatus.push(jobId);
      const status = statuses[Math.min(tick, statuses.length - 1)];
      tick++;
      return { status, error: null };
    },
    async getBuildSummary() {
      return summaryDoc();
    },
  });
  let trackedJob: { jobId: string; label: string; derivedKey?: string } | null = null;
  deps.trackJob = (opts) => {
    trackedJob = opts;
  };
  const asset = await loadNode(deps, SCOPE, REF, {
    kind: "build",
    capability: "asset-build-fixture",
    options: {},
    fingerprintInputs: [],
    revision: "20260901T100000Z",
    provider: "fixture-lines",
  });
  assert.equal(calls.jobStatus.length, 3, "polled queued -> running -> done");
  assert.ok(calls.jobStatus.every((id) => id === "job-1"));
  assert.equal(trackedJob !== null && (trackedJob as { jobId: string }).jobId, "job-1");
  assert.equal(calls.loadModelFromUrl.length, 1);
  assert.equal(asset.glbKey, summaryDoc().glb_key);
});

test("a job that ends in error is refused, and nothing is loaded", async () => {
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse({ job_id: "job-err", cached: false });
    },
    async jobStatus() {
      return { status: "error", error: "worker blew up" };
    },
  });
  await assert.rejects(
    () =>
      loadNode(deps, SCOPE, REF, {
        kind: "build",
        capability: "asset-build-fixture",
        options: {},
        fingerprintInputs: [],
        revision: "20260901T100000Z",
        provider: "fixture-lines",
      }),
    (e: unknown) => e instanceof DeliveryError && /worker blew up/.test((e as Error).message),
  );
  assert.equal(calls.loadModelFromUrl.length, 0);
});

// --- build: summary refusals -------------------------------------------------------

const CLAIM_BUILD = {
  kind: "build" as const,
  capability: "asset-build-fixture",
  options: {},
  fingerprintInputs: [],
  revision: "20260901T100000Z",
  provider: "fixture-lines",
};

test("a summary whose provenance.revision disagrees is refused, the field named, nothing loaded", async () => {
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse();
    },
    async getBuildSummary() {
      return summaryDoc({
        provenance: { ...(summaryDoc().provenance as object), revision: "20260101T000000Z" },
      });
    },
  });
  await assert.rejects(
    () => loadNode(deps, SCOPE, REF, CLAIM_BUILD),
    (e: unknown) =>
      e instanceof DeliveryError &&
      /provenance\.revision/.test((e as Error).message) &&
      /20260101T000000Z/.test((e as Error).message) &&
      /20260901T100000Z/.test((e as Error).message),
  );
  assert.equal(calls.loadModelFromUrl.length, 0);
});

test("a summary whose provenance.fingerprint disagrees is refused, the field named", async () => {
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse();
    },
    async getBuildSummary() {
      return summaryDoc({ provenance: { ...(summaryDoc().provenance as object), fingerprint: "wrong-fp" } });
    },
  });
  await assert.rejects(
    () => loadNode(deps, SCOPE, REF, CLAIM_BUILD),
    (e: unknown) => e instanceof DeliveryError && /provenance\.fingerprint/.test((e as Error).message),
  );
  assert.equal(calls.loadModelFromUrl.length, 0);
});

test("a glb_key outside the derived prefix is refused, and nothing is loaded", async () => {
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse();
    },
    async getBuildSummary() {
      return summaryDoc({ glb_key: "_derived/assets/some-other-provider/elsewhere/model.glb" });
    },
  });
  await assert.rejects(
    () => loadNode(deps, SCOPE, REF, CLAIM_BUILD),
    (e: unknown) => e instanceof DeliveryError && /outside the prefix/.test((e as Error).message),
  );
  assert.equal(calls.loadModelFromUrl.length, 0);
});

// --- covered (ghost/below) loads: node scopes the build, subject names the ancestor ------

test("a covered load posts {node: <row>, subject: <ancestor>}, and the summary's provenance.node is checked against the row", async () => {
  const covered: NodeRef = { ...REF, node: "member-3" };
  let sentBody: unknown = null;
  const { deps, calls } = fakeDeps({
    async buildAssetNode(_scope, body) {
      sentBody = body;
      return buildResponse({
        node: "member-3",
        subject: "area-1",
        fingerprint: "fp-member-3",
        derived_key: "_derived/assets/fixture-lines/plant-a/area-1/20260901T100000Z/member-3/fp-member-3/summary.json",
      });
    },
    async getBuildSummary(_scope, key) {
      calls.getBuildSummary.push(key);
      return summaryDoc({
        glb_key: "_derived/assets/fixture-lines/plant-a/area-1/20260901T100000Z/member-3/fp-member-3/model.glb",
        provenance: {
          ...(summaryDoc().provenance as object),
          node: "member-3",
          fingerprint: "fp-member-3",
        },
      });
    },
  });
  const asset = await loadNode(deps, SCOPE, covered, CLAIM_BUILD);
  assert.deepEqual(sentBody, {
    provider: "fixture-lines",
    collection: "plant-a",
    node: "member-3",
    subject: "area-1",
    revision: "20260901T100000Z",
  });
  assert.equal(asset.sourceName, "assets:fixture-lines/plant-a/area-1@20260901T100000Z#member-3");
  assert.equal(calls.loadModelFromUrl.length, 1);
});

test("a covered build whose summary's provenance.node names the ancestor instead of the clicked row is refused", async () => {
  const covered: NodeRef = { ...REF, node: "member-3" };
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse({ node: "member-3", subject: "area-1" });
    },
    async getBuildSummary() {
      // A builder that (wrongly) scoped its geometry to the ancestor rather
      // than the requested node -- exactly the bug per-node scoping exists
      // to catch, so this must be refused, not silently accepted.
      return summaryDoc({ provenance: { ...(summaryDoc().provenance as object), node: "area-1" } });
    },
  });
  await assert.rejects(
    () => loadNode(deps, SCOPE, covered, CLAIM_BUILD),
    (e: unknown) => e instanceof DeliveryError && /provenance\.node/.test((e as Error).message),
  );
  assert.equal(calls.loadModelFromUrl.length, 0);
});

test("an unknown summary schema is refused outright", () => {
  assert.throws(
    () => parseBuildSummary({ schema: "ada.assets/build@2", ok: true }),
    (e: unknown) => e instanceof DeliveryError && /unknown build summary schema/.test((e as Error).message),
  );
});

test("validateBuildSummary refuses ok: false with the summary's own error", () => {
  const summary = parseBuildSummary(summaryDoc({ ok: false, error: "no geometry produced" }));
  assert.throws(
    () =>
      validateBuildSummary(summary, {
        provider: "fixture-lines",
        collection: "plant-a",
        subject: "area-1",
        revision: "20260901T100000Z",
        node: "area-1",
        fingerprint: "fp123",
        derivedPrefix: "_derived/assets/fixture-lines/plant-a/area-1/20260901T100000Z/all/fp123",
      }),
    (e: unknown) => e instanceof DeliveryError && /no geometry produced/.test((e as Error).message),
  );
});

// --- dedup: already-loaded source names ---------------------------------------------

test("a second load of an already-loaded source name does not re-fetch", async () => {
  const { deps, calls, loadedNames } = fakeDeps({
    async buildAssetNode() {
      calls.buildAssetNode.push({});
      throw new Error("must not be called for an already-loaded source");
    },
  });
  loadedNames.add(assetSourceName(REF));
  const asset = await loadNode(deps, SCOPE, REF, CLAIM_BUILD);
  assert.equal(calls.buildAssetNode.length, 0);
  assert.equal(calls.loadModelFromUrl.length, 0);
  assert.equal(asset.sourceName, assetSourceName(REF));
});

test("a second mesh load of an already-loaded source name does not call the scene loader again", async () => {
  const { deps, calls, loadedNames } = fakeDeps();
  loadedNames.add(assetSourceName(REF));
  const claim: WireDeliveryClaim = {
    kind: "mesh",
    url: "https://cdn.example/a.glb",
    source_up_axis: "z",
    revision: REF.revision,
    provider: REF.provider,
  };
  await loadNode(deps, SCOPE, REF, parseDeliveryClaim(claim));
  assert.equal(calls.loadModelFromUrl.length, 0);
});

test("a build that never reaches a terminal state is given up on, naming the capability", async () => {
  // The state this guards is ordinary, not exotic: a `build` claim routes to the pool
  // advertising its capability, and a capability no pool advertises leaves a perfectly healthy
  // job unpicked for ever. A spinner that never resolves is the one reading of that which is
  // certainly wrong -- so the wait is bounded, the message names the capability nobody serves,
  // and it says the job was not cancelled.
  const { deps, calls } = fakeDeps({
    async buildAssetNode() {
      return buildResponse({ job_id: "job-forever", cached: false, capability: "asset-build-nobody-serves" });
    },
    async jobStatus() {
      return { status: "queued", error: null };
    },
  });
  let clock = 0;
  deps.wait = async (ms: number) => {
    clock += ms;
  };
  deps.now = () => clock;

  await assert.rejects(
    () => loadNode(deps, SCOPE, REF, CLAIM_BUILD),
    (err: Error) => {
      assert.match(err.message, /still queued after 10 min/);
      assert.match(err.message, /asset-build-nobody-serves/);
      assert.match(err.message, /was not cancelled/);
      return true;
    },
  );
  assert.equal(calls.getBuildSummary.length, 0, "the summary must never be read for a job that never finished");
  assert.equal(calls.loadModelFromUrl.length, 0);
});
