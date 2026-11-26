import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { LocalArtefactStore } from "@/services/compute/localArtefactStore";

describe("LocalArtefactStore (memory fallback & Publish to Scope)", () => {
  it("stores, retrieves, lists, and deletes artefacts scoped by (scope, key)", async () => {
    const store = new LocalArtefactStore({ allowOpfs: false });
    const bytesA = new TextEncoder().encode('{"ok":true}');
    const bytesB = new Uint8Array([1, 2, 3, 4]);

    await store.put("project:alpha", "_derived/a/summary.json", bytesA, "application/json");
    await store.put("project:alpha", "_derived/a/model.glb", bytesB, "model/gltf-binary");
    await store.put("project:beta", "_derived/a/summary.json", bytesB, "application/json");

    assert.equal(await store.has("project:alpha", "_derived/a/summary.json"), true);
    assert.equal(await store.has("project:alpha", "missing"), false);

    const got = await store.get("project:alpha", "_derived/a/summary.json");
    assert.ok(got);
    assert.equal(got.storage, "memory");
    assert.equal(got.contentType, "application/json");
    assert.equal(new TextDecoder().decode(got.bytes), '{"ok":true}');

    const listed = store.list("project:alpha", "_derived/a/");
    assert.deepEqual(
      listed.map((m) => m.key),
      ["_derived/a/model.glb", "_derived/a/summary.json"],
    );

    await store.delete("project:alpha", "_derived/a/model.glb");
    assert.equal(await store.has("project:alpha", "_derived/a/model.glb"), false);
    assert.equal(await store.has("project:beta", "_derived/a/summary.json"), true);
  });

  it("evicts oldest entries when exceeding maxMemoryBytes", async () => {
    const store = new LocalArtefactStore({ allowOpfs: false, maxMemoryBytes: 10 });
    await store.put("shared", "k1", new Uint8Array(6));
    await store.put("shared", "k2", new Uint8Array(6));

    assert.equal(await store.has("shared", "k1"), false);
    assert.equal(await store.has("shared", "k2"), true);
  });

  it("publishes local artefacts to scope blobs and reports partial failures", async () => {
    const store = new LocalArtefactStore({ allowOpfs: false });
    await store.put("project:p1", "_derived/out.json", new TextEncoder().encode("{}"), "application/json");
    await store.put("project:p1", "_derived/fail.glb", new Uint8Array([9, 8]), "model/gltf-binary");

    const calls: Array<{ url: string; method?: string }> = [];
    const fakeFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method });
      if (url.includes("fail.glb")) {
        return new Response("forbidden", { status: 403, statusText: "Forbidden" });
      }
      return new Response(null, { status: 201 });
    }) as typeof fetch;

    const progress: Array<[number, number, string]> = [];
    const res = await store.publishToScope("project:p1", undefined, {
      apiBase: "/api",
      fetchImpl: fakeFetch,
      onProgress: (done, total, key) => progress.push([done, total, key]),
    });

    assert.deepEqual(res.published, ["_derived/out.json"]);
    assert.equal(res.errors.length, 1);
    assert.equal(res.errors[0].key, "_derived/fail.glb");
    assert.match(res.errors[0].error, /403/);
    assert.equal(calls.length, 2);
    assert.equal(calls[0].method, "PUT");
    assert.equal(progress.length, 2);
  });
});
