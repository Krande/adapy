import { strict as assert } from "node:assert";
import { test } from "node:test";

import { bindingFor } from "../../services/externalModelsBinding";

test("bindingFor resolves provider:collection", () => {
  const b = bindingFor({ "project:1": "vendor:alpha" }, "project:1");
  assert.deepEqual(b, { provider: "vendor", collection: "alpha" });
});

test("bindingFor treats a bare value as a collection on the default provider", () => {
  // The shape a single-provider deployment naturally writes.
  const b = bindingFor({ shared: "alpha" }, "shared");
  assert.deepEqual(b, { provider: "demo", collection: "alpha" });
});

test("bindingFor returns null for an unbound scope", () => {
  assert.equal(bindingFor({}, "shared"), null);
});

test("bindingFor rejects a half-written binding rather than guessing", () => {
  // An unbound panel must stay off, not bind to an empty collection.
  assert.equal(bindingFor({ shared: "vendor:" }, "shared"), null);
  assert.equal(bindingFor({ shared: ":alpha" }, "shared"), null);
});

test("bindingFor keeps colons inside the collection name", () => {
  // Only the FIRST colon separates; a collection may contain one.
  const b = bindingFor({ shared: "vendor:a:b" }, "shared");
  assert.deepEqual(b, { provider: "vendor", collection: "a:b" });
});
