import { strict as assert } from "node:assert";
import { test } from "node:test";

import { bindingFor, boundCollectionOption } from "../../services/externalModelsBinding";

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

// boundCollectionOption — a bound <select> must render its own value.
//
// A <select> whose value matches no <option> falls back to its first one, so a
// bound scope displayed "— none —" until the collection list arrived. Since
// collections are fetched on demand, that was the state on first paint.

test("boundCollectionOption supplies the option while the list is unfetched", () => {
  // undefined = not fetched. Without an option the bound value cannot display.
  const o = boundCollectionOption("alpha", undefined);
  assert.deepEqual(o, { value: "alpha", label: "alpha" });
});

test("boundCollectionOption does not call an unfetched binding stale", () => {
  // "(not in list)" would be a claim we cannot make before looking.
  assert.equal(boundCollectionOption("alpha", undefined)?.label, "alpha");
});

test("boundCollectionOption marks a value the loaded list lacks", () => {
  const o = boundCollectionOption("alpha", [{ id: "beta" }]);
  assert.deepEqual(o, { value: "alpha", label: "alpha (not in list)" });
});

test("boundCollectionOption marks a value when the loaded list is empty", () => {
  // [] is loaded-and-empty, unlike undefined.
  assert.deepEqual(boundCollectionOption("alpha", []), {
    value: "alpha",
    label: "alpha (not in list)",
  });
});

test("boundCollectionOption adds nothing when the list already carries it", () => {
  // A duplicate <option> would render the id twice in the dropdown.
  assert.equal(boundCollectionOption("alpha", [{ id: "alpha" }]), null);
});

test("boundCollectionOption adds nothing for an unbound scope", () => {
  assert.equal(boundCollectionOption("", undefined), null);
  assert.equal(boundCollectionOption("", [{ id: "alpha" }]), null);
});
