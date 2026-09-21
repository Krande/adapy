/**
 * The TypeScript half of the shared grammar vectors.
 *
 * Reads the SAME JSON the Python suite does (tests/core/assets/vectors/asset_key_vectors.json).
 * Two implementations of one grammar drift unless something forces them together; this is that
 * something. A case added on one side without the other fails here.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  ASSET_KEY_SEGMENTS,
  AssetKeyError,
  assetKey,
  isCollectionLevel,
  parseAssetKey,
  revisionFromInstant,
  stagingPrefix,
} from "../../assets/keys";

const here = dirname(fileURLToPath(import.meta.url));
const vectorsPath = resolve(here, "../../../../../tests/core/assets/vectors/asset_key_vectors.json");
const vectors = JSON.parse(readFileSync(vectorsPath, "utf-8"));

test("the vectors file is the one Python reads", () => {
  assert.ok(Array.isArray(vectors.valid) && vectors.valid.length > 0);
  assert.equal(ASSET_KEY_SEGMENTS, 5);
});

for (const c of vectors.valid) {
  test(`valid: ${c.why}`, () => {
    const parsed = parseAssetKey(c.key);
    assert.equal(parsed.collection, c.collection);
    assert.equal(parsed.subject, c.subject);
    assert.equal(parsed.revision, c.revision);
    assert.equal(parsed.filename, c.filename);
    assert.equal(isCollectionLevel(parsed), c.collection_level);
    assert.equal(assetKey(parsed.collection, parsed.subject, parsed.revision, parsed.filename), c.key);
  });
}

for (const c of vectors.invalid) {
  test(`invalid: ${c.why}`, () => {
    assert.throws(() => parseAssetKey(c.key), AssetKeyError);
  });
}

for (const c of vectors.instants) {
  test(`instant: ${c.why}`, () => {
    assert.equal(revisionFromInstant(c.in), c.out);
  });
}

for (const naive of vectors.naive_instants) {
  test(`naive instant refused: ${naive}`, () => {
    assert.throws(() => revisionFromInstant(naive), AssetKeyError);
  });
}

test("lexical order is chronological order", () => {
  const chronological: string[] = vectors.ordering.chronological;
  assert.deepEqual([...chronological].sort(), chronological);
});

test("a staging prefix has four segments once a filename is appended", () => {
  const staged = `${stagingPrefix("upload-123")}source.bin`;
  assert.equal(staged.split("/").length, 4);
  assert.throws(() => parseAssetKey(staged), AssetKeyError);
});
