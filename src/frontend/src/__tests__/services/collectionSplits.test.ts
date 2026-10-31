import { strict as assert } from "node:assert";
import { test } from "node:test";

import { assetProviderCollections } from "../../services/assetScopeCollections";
import {
  applySplits,
  declaredCopies,
  parseCollectionSplits,
  serialiseCollectionSplits,
  withSplit,
} from "../../services/collectionSplits";

const spec = {
  id: "exporter",
  asset_provider_id: "vendor",
  asset_collections_field: "items",
  items: ["BETA", "ALPHA"],
  asset_collection_splits_field: "copies",
  copies: {
    ALPHA: [
      { collection: "ALPHA-X", label: "ALPHA (X)", location: "East", release: "R1", last_written: "2026-01-02", default: true },
      { collection: "ALPHA-Y", label: "ALPHA (Y)" },
      { label: "no id" },
    ],
    BETA: "not a list",
  },
};

test("declaredCopies reads the named field and drops unusable entries", () => {
  const copies = declaredCopies(spec);
  assert.deepEqual(Object.keys(copies), ["ALPHA"]);
  assert.deepEqual(copies.ALPHA.map((c) => c.collection), ["ALPHA-X", "ALPHA-Y"]);
  assert.equal(copies.ALPHA[0].location, "East");
  assert.equal(copies.ALPHA[0].isDefault, true);
  assert.equal(copies.ALPHA[1].label, "ALPHA (Y)");
  assert.deepEqual(declaredCopies({ items: [] }), {});
});

test("parseCollectionSplits is lenient and keys collections in lower case", () => {
  assert.deepEqual(parseCollectionSplits('{"vendor": {"ALPHA": ["ALPHA-X", "", 3], "B": "x", "C": []}}'), {
    vendor: { alpha: ["ALPHA-X"] },
  });
  assert.deepEqual(parseCollectionSplits("{nope"), {});
  assert.deepEqual(parseCollectionSplits(null), {});
});

test("applySplits replaces a split collection by its copies, and falls back when they are gone", () => {
  const copies = declaredCopies(spec);
  assert.deepEqual(applySplits(["ALPHA", "BETA"], copies, undefined), ["ALPHA", "BETA"]);
  assert.deepEqual(applySplits(["ALPHA", "BETA"], copies, { alpha: ["alpha-y", "ALPHA-X"] }), ["ALPHA-Y", "ALPHA-X", "BETA"]);
  assert.deepEqual(applySplits(["ALPHA", "BETA"], copies, { alpha: ["ALPHA-GONE"] }), ["ALPHA", "BETA"]);
});

test("withSplit sets and clears one collection without touching others", () => {
  let map = withSplit({}, "vendor", "ALPHA", ["ALPHA-X"]);
  map = withSplit(map, "vendor", "GAMMA", ["GAMMA-1"]);
  assert.deepEqual(map, { vendor: { alpha: ["ALPHA-X"], gamma: ["GAMMA-1"] } });
  map = withSplit(map, "vendor", "alpha", []);
  assert.deepEqual(map, { vendor: { gamma: ["GAMMA-1"] } });
  assert.deepEqual(withSplit(map, "vendor", "GAMMA", []), {});
  assert.deepEqual(parseCollectionSplits(serialiseCollectionSplits({ vendor: { alpha: ["ALPHA-X"] } })), {
    vendor: { alpha: ["ALPHA-X"] },
  });
});

test("assetProviderCollections offers a split collection as its copies and exposes what it declares", () => {
  const [unsplit] = assetProviderCollections([spec]);
  assert.deepEqual(unsplit.collections, ["ALPHA", "BETA"]);
  assert.deepEqual(Object.keys(unsplit.copies), ["ALPHA"]);
  const [split] = assetProviderCollections([spec], { vendor: { alpha: ["ALPHA-Y"] } });
  assert.deepEqual(split.collections, ["ALPHA-Y", "BETA"]);
});
