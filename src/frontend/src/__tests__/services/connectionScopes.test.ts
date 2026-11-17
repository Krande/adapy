import assert from "node:assert/strict";
import { test } from "node:test";

import {
  offersIn,
  parseConnectionScopes,
  scopesFor,
  serialiseConnectionScopes,
  settingScopeFor,
  toggleScope,
  withScopes,
} from "@/services/connectionScopes";

test("an unset or unreadable setting offers every pool's specs everywhere", () => {
  for (const raw of [null, "", "not json", "[]", '"x"']) {
    const map = parseConnectionScopes(raw);
    assert.deepEqual(map, {});
    assert.equal(offersIn(map, "pool-a", "project:p1"), true);
  }
});

test("a restricted pool is offered only in its scopes; personal scopes are one entry", () => {
  let map = withScopes({}, "pool-a", ["project:p1"]);
  assert.equal(offersIn(map, "pool-a", "project:p1"), true);
  assert.equal(offersIn(map, "pool-a", "shared"), false);
  assert.equal(offersIn(map, "other", "shared"), true);
  map = toggleScope(map, "pool-a", settingScopeFor("user:abc"), true);
  assert.deepEqual(scopesFor(map, "pool-a"), ["project:p1", "user"]);
  assert.equal(offersIn(map, "pool-a", "user:someone-else"), true);
  map = toggleScope(map, "pool-a", "project:p1", false);
  assert.deepEqual(scopesFor(map, "pool-a"), ["user"]);
});

test("back to every scope drops the entry; the value round-trips", () => {
  const map = withScopes(withScopes({}, "pool-a", ["shared"]), "pool-a", null);
  assert.deepEqual(map, {});
  const stored = withScopes({}, "pool-a", ["shared", "project:p1", "shared"]);
  assert.deepEqual(parseConnectionScopes(serialiseConnectionScopes(stored)), { "pool-a": ["project:p1", "shared"] });
});
