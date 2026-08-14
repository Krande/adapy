import assert from "node:assert/strict";
import { test } from "node:test";

import {
  addMetadataKey,
  asMetaObject,
  formatMetadataValue,
  metaOrNull,
  parseMetadataValue,
  removeMetadataKey,
  renameMetadataKey,
  setMetadataValue,
} from "../../utils/cellbuilder/metadata";

// These pure helpers ARE the Metadata editor's map math: the selection panel
// wires its rows straight to them, and the store persists the resulting map
// verbatim. No metadata KEY is hardcoded anywhere — values are an opaque
// string -> (string|number|bool|json) space.

test("asMetaObject coerces absent/malformed values to an empty map", () => {
  assert.deepEqual(asMetaObject(undefined), {});
  assert.deepEqual(asMetaObject(null), {});
  assert.deepEqual(asMetaObject("nope"), {});
  assert.deepEqual(asMetaObject(5), {});
  assert.deepEqual(asMetaObject([1, 2]), {}); // arrays are not a map
  const m = { a: 1 };
  assert.equal(asMetaObject(m), m); // passes an object map through by reference
});

test("parseMetadataValue recognises bools and numbers, keeps everything else text", () => {
  assert.equal(parseMetadataValue("true"), true);
  assert.equal(parseMetadataValue("false"), false);
  assert.equal(parseMetadataValue("5"), 5);
  assert.equal(parseMetadataValue("-3.5"), -3.5);
  assert.equal(parseMetadataValue("1e3"), 1000);
  assert.equal(parseMetadataValue(".5"), 0.5);
  // Not unambiguous numbers/bools -> stay strings.
  assert.equal(parseMetadataValue("TRUE"), "TRUE");
  assert.equal(parseMetadataValue("5 kg"), "5 kg");
  assert.equal(parseMetadataValue("1,2"), "1,2");
  assert.equal(parseMetadataValue("0x1"), "0x1");
  assert.equal(parseMetadataValue("Infinity"), "Infinity");
  assert.equal(parseMetadataValue("v5"), "v5");
});

test("parseMetadataValue preserves the ORIGINAL string (whitespace) when it stays text", () => {
  assert.equal(parseMetadataValue(""), "");
  assert.equal(parseMetadataValue("  "), "  ");
  assert.equal(parseMetadataValue("  hi  "), "  hi  ");
});

test("parseMetadataValue parses JSON object/array literals, malformed stays text", () => {
  assert.deepEqual(parseMetadataValue('{"a":1}'), { a: 1 });
  assert.deepEqual(parseMetadataValue("[1,2,3]"), [1, 2, 3]);
  assert.equal(parseMetadataValue("{oops"), "{oops");
  assert.equal(parseMetadataValue("{a: 1}"), "{a: 1}"); // not valid JSON
});

test("formatMetadataValue is the display inverse of parse for round-trippable forms", () => {
  assert.equal(formatMetadataValue("hi"), "hi");
  assert.equal(formatMetadataValue(5), "5");
  assert.equal(formatMetadataValue(true), "true");
  assert.equal(formatMetadataValue({ a: 1 }), '{"a":1}');
  // round-trip: parse(format(x)) === x for the typed forms
  for (const v of [true, false, 5, -3.5] as const) {
    assert.equal(parseMetadataValue(formatMetadataValue(v)), v);
  }
  assert.deepEqual(parseMetadataValue(formatMetadataValue({ a: 1 })), { a: 1 });
});

test("setMetadataValue parses and always returns a new map", () => {
  const m = { a: "x" };
  const next = setMetadataValue(m, "b", "7");
  assert.deepEqual(next, { a: "x", b: 7 });
  assert.notEqual(next, m); // new ref (edit is always a commit)
  assert.deepEqual(m, { a: "x" }); // input untouched
});

test("renameMetadataKey preserves order; no-op returns the same ref", () => {
  const m = { a: 1, b: 2, c: 3 };
  const renamed = renameMetadataKey(m, "b", "bee");
  assert.deepEqual(Object.keys(renamed), ["a", "bee", "c"]); // order kept
  assert.equal(renamed.bee, 2);
  // no-ops all return the SAME reference (so the caller skips committing)
  assert.equal(renameMetadataKey(m, "b", "b"), m); // unchanged
  assert.equal(renameMetadataKey(m, "b", "  "), m); // blank
  assert.equal(renameMetadataKey(m, "b", "a"), m); // duplicate target
  assert.equal(renameMetadataKey(m, "zzz", "q"), m); // missing source
});

test("removeMetadataKey drops a key; absent key returns the same ref", () => {
  const m = { a: 1, b: 2 };
  assert.deepEqual(removeMetadataKey(m, "a"), { b: 2 });
  assert.equal(removeMetadataKey(m, "zzz"), m);
});

test("addMetadataKey appends a unique empty field", () => {
  assert.deepEqual(addMetadataKey({}), { key: "" });
  assert.deepEqual(addMetadataKey({ key: "x" }), { key: "x", key1: "" });
  assert.deepEqual(addMetadataKey({ key: "x", key1: "y" }), {
    key: "x",
    key1: "y",
    key2: "",
  });
});

test("metaOrNull collapses an empty map to null (drops the METADATA key)", () => {
  assert.equal(metaOrNull({}), null);
  const m = { a: 1 };
  assert.equal(metaOrNull(m), m);
});
