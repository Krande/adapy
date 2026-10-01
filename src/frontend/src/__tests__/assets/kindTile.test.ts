import { strict as assert } from "node:assert";
import { test } from "node:test";

import { kindTile } from "../../assets/kindTile";

test("a kind always gets the same tile, whatever its case or padding", () => {
  assert.deepEqual(kindTile("SITE"), kindTile(" site "));
});

test("two letters keep kinds that share a first letter apart", () => {
  assert.equal(kindTile("site").letters, "Si");
  assert.equal(kindTile("stru").letters, "St");
  assert.equal(kindTile("sbfr").letters, "Sb");
});

test("an empty kind still gets a tile", () => {
  assert.equal(kindTile("").letters, "·");
});

test("kinds spread over more than one colour", () => {
  const colours = new Set(["site", "zone", "stru", "frmw", "sbfr", "gensec", "panel", "equi"].map((k) => kindTile(k).fg));
  assert.ok(colours.size >= 4, `${colours.size} colours`);
});
