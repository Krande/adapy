// A gzipped model's bytes come back whole, as a Blob of the type asked for.
import {test} from "node:test";
import assert from "node:assert/strict";
import {gzipSync} from "node:zlib";

import {gunzipToBlob} from "@/utils/gunzipToBlob";

test("gunzipToBlob returns the original bytes with the given type", async () => {
    const original = new Uint8Array(200_000);
    for (let i = 0; i < original.length; i++) original[i] = (i * 31 + (i >> 5)) & 255;
    const blob = await gunzipToBlob(new Uint8Array(gzipSync(original)), "model/gltf-binary");
    assert.equal(blob.type, "model/gltf-binary");
    assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), original);
});

test("a view into a larger buffer is inflated from its own bytes only", async () => {
    const zipped = new Uint8Array(gzipSync(new TextEncoder().encode("a model")));
    const message = new Uint8Array(zipped.length + 64);
    message.set(zipped, 32);
    const blob = await gunzipToBlob(message.subarray(32, 32 + zipped.length), "application/octet-stream");
    assert.equal(await blob.text(), "a model");
});
