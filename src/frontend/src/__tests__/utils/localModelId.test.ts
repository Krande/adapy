import assert from "node:assert/strict";
import { test } from "node:test";

import { localModelIdFromSourceName } from "@/utils/cellbuilder/localModelId";

// Must stay in lockstep with the backend's model_id regex
// (ada.comms.msg_handling.save_procedural_model._MODEL_ID_RE): letters/digits/'.'/'_'/'-' only,
// 1-128 characters, starting with a letter or digit.
const VALID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

test("a plain alnum source name passes through unchanged", () => {
  assert.equal(localModelIdFromSourceName("hull-a"), "hull-a");
});

test("spaces, slashes and colons become dashes", () => {
  const id = localModelIdFromSourceName("My Model: v2/final");
  assert.match(id, VALID);
  assert.equal(id, "My-Model-v2-final");
});

test("a leading non-alnum character is stripped, not dashed", () => {
  assert.match(localModelIdFromSourceName("-leading-dash"), VALID);
  assert.match(localModelIdFromSourceName("...dots"), VALID);
});

test("empty, whitespace-only or missing names fall back to a fixed id", () => {
  assert.equal(localModelIdFromSourceName(""), "model");
  assert.equal(localModelIdFromSourceName("   "), "model");
  assert.equal(localModelIdFromSourceName(null), "model");
  assert.equal(localModelIdFromSourceName(undefined), "model");
});

test("a name that sanitises to nothing (all separators) falls back too", () => {
  assert.equal(localModelIdFromSourceName("://///"), "model");
});

test("longer than 128 characters is truncated", () => {
  const long = "a".repeat(200);
  const id = localModelIdFromSourceName(long);
  assert.equal(id.length, 128);
  assert.match(id, VALID);
});

test("every generated id matches the backend's model_id pattern", () => {
  for (const name of ["a", "Model 1.0 (final)", "path\\to\\thing", "проект", "___", "a".repeat(500)]) {
    assert.match(localModelIdFromSourceName(name), VALID, `for input ${JSON.stringify(name)}`);
  }
});
