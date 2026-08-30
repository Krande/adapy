// What gets PUT, and under which headers.
//
// The provider says how it wants a model stored; this decides only whether the
// bytes in hand already satisfy that. Both ways of getting it wrong are silent
// and produce a file the viewer cannot read while every header claims it is
// fine — one catalogue was found holding exactly that, one object out of forty.

import assert from "node:assert/strict";
import test from "node:test";

import { isGzipped, prepareUploadBody } from "@/services/externalModelUpload";

const GZIP_WANTED = { "Content-Type": "model/gltf-binary", "Content-Encoding": "gzip" };

/** A gzip member's first bytes. Enough: only the magic is inspected. */
const gzipBlob = (rest = "payload") => new Blob([new Uint8Array([0x1f, 0x8b, 0x08, 0x00]), rest]);
const glbBlob = () => new Blob([new Uint8Array([0x67, 0x6c, 0x54, 0x46]), "x".repeat(4096)]);

test("the gzip magic is what identifies an already-compressed body", () => {
  assert.equal(isGzipped(new Uint8Array([0x1f, 0x8b])), true);
  assert.equal(isGzipped(new Uint8Array([0x67, 0x6c, 0x54, 0x46])), false, "raw glTF");
  assert.equal(isGzipped(new Uint8Array([0x1f])), false, "one byte cannot say");
  assert.equal(isGzipped(new Uint8Array([])), false);
});

test("a raw glTF is compressed on the way up, and stays labelled gzip", async () => {
  const file = glbBlob();
  const { body, headers } = await prepareUploadBody(file, GZIP_WANTED);
  assert.equal(headers["Content-Encoding"], "gzip");
  assert.ok(body.size < file.size, "4 KB of repeated bytes must compress");
  assert.ok(isGzipped(new Uint8Array(await body.slice(0, 2).arrayBuffer())));
});

test("an already-gzipped file is NOT compressed again", async () => {
  // The mirror of the original bug. A second pass produces a file the viewer
  // cannot read, while `Content-Encoding: gzip` still describes it correctly —
  // so nothing anywhere would name the fault.
  const file = gzipBlob();
  const { body, headers } = await prepareUploadBody(file, GZIP_WANTED);
  assert.equal(body, file, "the same blob, untouched");
  assert.equal(headers["Content-Encoding"], "gzip");
});

test("a provider that does not ask for gzip gets the bytes as they are", async () => {
  const file = glbBlob();
  const headers = { "Content-Type": "model/gltf-binary" };
  const out = await prepareUploadBody(file, headers);
  assert.equal(out.body, file);
  assert.deepEqual(out.headers, headers);
});

test("without CompressionStream the header is DROPPED, not kept", async () => {
  // Uncompressed and honest beats compressed and wrong: keeping the header here
  // would upload plain bytes labelled gzip, which is the same corruption from
  // the other side.
  const g = globalThis as { CompressionStream?: unknown };
  const saved = g.CompressionStream;
  delete g.CompressionStream;
  try {
    const file = glbBlob();
    const { body, headers } = await prepareUploadBody(file, GZIP_WANTED);
    assert.equal(body, file);
    assert.equal(headers["Content-Encoding"], undefined);
    assert.equal(headers["Content-Type"], "model/gltf-binary", "the type still travels");
  } finally {
    if (saved !== undefined) g.CompressionStream = saved;
  }
});
