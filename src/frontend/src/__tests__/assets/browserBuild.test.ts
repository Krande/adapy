// Asset node builds in the browser (`@/assets/delivery`'s routing, `@/assets/buildUpload`'s frame,
// `services/compute/assetBuild`'s route). Fakes only: no network, no Pyodide.
//
// Pins:
//   - with a browser path and the browser first, the build is asked with `browser: true`, run in
//     the browser, uploaded, and the stored summary is read and validated -- no job is polled;
//   - a plan that answers `cached` runs nothing;
//   - a failed browser build falls to the worker as an explicit choice (`runOnWorker`) when the
//     order lists it, and is rethrown when it does not;
//   - no browser path: the worker builds it, asked exactly as before;
//   - the upload frame is what `ada/assets/build_upload.py` reads.

import assert from "node:assert/strict";
import { test } from "node:test";

import { encodeBuildUpload, filesUnderPrefix } from "../../assets/buildUpload";
import {
  parseDeliveryClaim,
  prepareNode,
  type BrowserAssetBuild,
  type DeliveryApi,
  type LoadNodeDeps,
  type NodeRef,
} from "../../assets/delivery";
import type { WireBuildAssetResponse, WireDeliveryClaim } from "../../assets/types";
import { browserAssetBuild } from "../../services/compute/assetBuild";

const SCOPE = "project:p1";
const REF: NodeRef = { provider: "e2e-ifc", collection: "plant", subject: "site-b", revision: "20261127T100000Z" };
const PREFIX = "_derived/assets/e2e-ifc/plant/site-b/20261127T100000Z/site-b/fp1";
const CLAIM = () =>
  parseDeliveryClaim({ kind: "build", capability: "e2e-ifc-build", revision: REF.revision, provider: "e2e-ifc" } as WireDeliveryClaim);

function planned(over: Partial<WireBuildAssetResponse> = {}): WireBuildAssetResponse {
  return {
    derived_key: `${PREFIX}/summary.json`,
    capability: "e2e-ifc-build",
    provider: "e2e-ifc",
    subject: "site-b",
    revision: REF.revision,
    node: "site-b",
    fingerprint: "fp1",
    job_id: null,
    cached: false,
    execution: "browser",
    build: {
      provider: "e2e-ifc",
      collection: "plant",
      subject: "site-b",
      revision: REF.revision,
      node: "site-b",
      fingerprint: "fp1",
      hierarchy_source: REF.revision,
      capability: "e2e-ifc-build",
      options: { source_key: "assets/plant/site-b/x/source.ifc" },
      derived_prefix: PREFIX,
      exclude_nodes: [],
    },
    ...over,
  };
}

function summary(): Record<string, unknown> {
  return {
    schema: "ada.assets/build@1",
    ok: true,
    glb_key: `${PREFIX}/model.glb`,
    provenance: {
      provider: "e2e-ifc",
      collection: "plant",
      subject: "site-b",
      revision: REF.revision,
      node: "site-b",
      fingerprint: "fp1",
      built_at: "2026-11-27T10:00:00Z",
    },
    counts: { drawn: 3 },
  };
}

interface Log {
  build: { body: Record<string, unknown>; runOnWorker?: boolean }[];
  runs: number;
  jobStatus: number;
}

function deps(opts: {
  route: Awaited<ReturnType<BrowserAssetBuild["route"]>>;
  plan?: WireBuildAssetResponse;
  runFails?: boolean;
}): { deps: LoadNodeDeps; log: Log } {
  const log: Log = { build: [], runs: 0, jobStatus: 0 };
  const api: DeliveryApi = {
    async buildAssetNode(_scope, body, o) {
      log.build.push({ body: body as Record<string, unknown>, runOnWorker: o?.runOnWorker });
      if (body.browser) return opts.plan ?? planned();
      return planned({ execution: undefined, build: undefined, job_id: "job-1" });
    },
    async getBuildSummary() {
      return summary();
    },
    async jobStatus() {
      log.jobStatus++;
      return { status: "done", error: null };
    },
  };
  const browserBuild: BrowserAssetBuild = {
    async route() {
      return opts.route;
    },
    async run(scope, built, request) {
      log.runs++;
      assert.equal(scope, SCOPE);
      assert.equal(built.build?.derived_prefix, PREFIX);
      assert.equal(request.revision, REF.revision);
      if (opts.runFails) throw new Error("ifc reader missing");
    },
  };
  return {
    deps: {
      api,
      browserBuild,
      loadModelFromUrl: async () => undefined,
      isLoaded: () => false,
      blobUrl: (scope, key) => `blob://${scope}/${key}`,
      wait: () => Promise.resolve(),
    },
    log,
  };
}

test("the browser builds it, uploads it, and the stored summary is read -- no job", async () => {
  const { deps: d, log } = deps({ route: { engines: ["pyodide", "worker"], browserPath: true } });
  const p = await prepareNode(d, SCOPE, REF, CLAIM());
  assert.equal(log.build.length, 1);
  assert.equal(log.build[0].body.browser, true);
  assert.equal(log.runs, 1);
  assert.equal(log.jobStatus, 0);
  assert.equal(p.glbKey, `${PREFIX}/model.glb`);
});

test("a plan answered from the store runs nothing", async () => {
  const { deps: d, log } = deps({
    route: { engines: ["pyodide", "worker"], browserPath: true },
    plan: planned({ cached: true, execution: undefined, build: undefined }),
  });
  await prepareNode(d, SCOPE, REF, CLAIM());
  assert.equal(log.runs, 0);
  assert.equal(log.jobStatus, 0);
});

test("a failed browser build falls to the worker as an explicit choice", async () => {
  const { deps: d, log } = deps({ route: { engines: ["pyodide", "worker"], browserPath: true }, runFails: true });
  await prepareNode(d, SCOPE, REF, CLAIM());
  assert.equal(log.build.length, 2);
  assert.equal(log.build[1].body.browser, undefined);
  assert.equal(log.build[1].runOnWorker, true);
  assert.equal(log.jobStatus, 1, "the worker job is polled");
});

test("a failed browser build without the worker in the order is the error", async () => {
  const { deps: d, log } = deps({ route: { engines: ["pyodide"], browserPath: true }, runFails: true });
  await assert.rejects(prepareNode(d, SCOPE, REF, CLAIM()), /ifc reader missing/);
  assert.equal(log.build.length, 1);
});

test("the worker first in the order builds it on the worker", async () => {
  const { deps: d, log } = deps({ route: { engines: ["worker", "pyodide"], browserPath: true } });
  await prepareNode(d, SCOPE, REF, CLAIM());
  assert.equal(log.runs, 0);
  assert.equal(log.build[0].runOnWorker, true);
});

test("no browser path: the worker builds it, asked as before", async () => {
  const { deps: d, log } = deps({ route: { engines: ["worker"], browserPath: false } });
  await prepareNode(d, SCOPE, REF, CLAIM());
  assert.equal(log.build.length, 1);
  assert.equal(log.build[0].body.browser, undefined);
  assert.equal(log.build[0].runOnWorker, undefined);
});

test("the upload frame: header length, JSON header, the files back to back", () => {
  const glb = new Uint8Array([103, 108, 84, 70, 1, 2, 3]);
  const notes = new TextEncoder().encode("{}");
  const frame = encodeBuildUpload(
    { provider: "e2e-ifc", collection: "plant", node: "site-b", revision: REF.revision },
    summary(),
    [
      { name: "model.glb", data: glb },
      { name: "notes.json", data: notes, contentEncoding: "gzip" },
    ],
  );
  const len = new DataView(frame.buffer).getUint32(0, true);
  const header = JSON.parse(new TextDecoder().decode(frame.slice(4, 4 + len)));
  assert.deepEqual(header.files, [
    { name: "model.glb", size: 7 },
    { name: "notes.json", size: 2, content_encoding: "gzip" },
  ]);
  assert.equal(header.request.node, "site-b");
  assert.deepEqual(Array.from(frame.slice(4 + len, 4 + len + 7)), Array.from(glb));
  assert.equal(frame.byteLength, 4 + len + 9);
});

test("a file written outside the derived prefix is refused before anything is sent", () => {
  const ok = filesUnderPrefix(PREFIX, [{ key: `${PREFIX}/model.glb`, bytes: new Uint8Array(1) }]);
  assert.deepEqual(ok.map((f) => f.name), ["model.glb"]);
  assert.throws(() => filesUnderPrefix(PREFIX, [{ key: "_derived/other/model.glb", bytes: new Uint8Array(1) }]), /outside/);
  assert.throws(() => filesUnderPrefix(PREFIX, [{ key: `${PREFIX}/a/b.glb`, bytes: new Uint8Array(1) }]), /outside/);
});

test("the route: a browser path only with a baked wheel for the capability, browser first by default", async () => {
  const withWheel = browserAssetBuild({ hasWheel: async () => true, refresh: async () => undefined });
  const route = await withWheel.route("e2e-ifc-build");
  assert.equal(route.browserPath, true);
  assert.equal(route.engines[0], "pyodide");
  const without = browserAssetBuild({ hasWheel: async () => false, refresh: async () => undefined });
  assert.deepEqual(await without.route("asset-build-other"), { engines: ["worker"], browserPath: false });
});

test("the run uploads what the builder wrote, under its plain names", async () => {
  const uploads: unknown[] = [];
  const build = browserAssetBuild({
    apiBase: "/api",
    runInBrowser: async (o) => {
      assert.equal(o.pluginId, "e2e-ifc-build");
      assert.equal(o.entrypoint, "ada.assets.wasm_build:run_job");
      return { summary: summary(), localArtefacts: [{ key: `${PREFIX}/model.glb`, bytes: new Uint8Array([1]), contentType: "model/gltf-binary" }] };
    },
    upload: async (scope, request, s, files) => {
      uploads.push({ scope, request, s, files: files.map((f) => f.name) });
      return { derived_key: `${PREFIX}/summary.json`, stored: true, cached: false };
    },
  });
  await build.run(SCOPE, planned(), { provider: "e2e-ifc", collection: "plant", node: "site-b", revision: REF.revision });
  assert.equal(uploads.length, 1);
  assert.deepEqual((uploads[0] as { files: string[] }).files, ["model.glb"]);
});
