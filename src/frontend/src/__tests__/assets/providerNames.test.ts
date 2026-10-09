import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  MAX_PROVIDER_LABEL_LENGTH,
  cleanProviderLabel,
  labelProblem,
  parseProviderAliases,
  parseProviderLabels,
  providerDisplayName,
  providerIdTitle,
  serialiseProviderAliases,
  withProviderAlias,
} from "../../assets/providerNames";

test("providerDisplayName: the first source naming the id wins, else the id", () => {
  const server = { "fixture-lines": "Lines (alias)" };
  const declared = { "fixture-lines": "Lines (spec)", mesher: "Mesher (spec)" };
  assert.equal(providerDisplayName("fixture-lines", server, declared), "Lines (alias)");
  assert.equal(providerDisplayName("mesher", server, declared), "Mesher (spec)");
  assert.equal(providerDisplayName("builder", server, declared), "builder");
  assert.equal(providerDisplayName("builder"), "builder");
  assert.equal(providerDisplayName("mesher", null, undefined, declared), "Mesher (spec)");
});

test("providerDisplayName: an empty label is no label", () => {
  assert.equal(providerDisplayName("mesher", { mesher: "" }), "mesher");
});

test("providerIdTitle keeps the raw id reachable", () => {
  assert.equal(providerIdTitle("fixture-lines"), "Provider id: fixture-lines");
});

test("parseProviderLabels keeps string labels only, trimmed", () => {
  assert.deepEqual(parseProviderLabels({ a: " A  b ", b: "", c: 3, d: null, "": "x" }), { a: "A b" });
  for (const raw of [null, undefined, "x", [1], 3]) assert.deepEqual(parseProviderLabels(raw), {});
});

test("cleanProviderLabel and labelProblem", () => {
  assert.equal(cleanProviderLabel("  a \n b "), "a b");
  assert.equal(cleanProviderLabel("   "), null);
  assert.equal(labelProblem(""), null, "empty clears, it is not a problem");
  assert.equal(labelProblem("x".repeat(MAX_PROVIDER_LABEL_LENGTH)), null);
  assert.match(labelProblem("x".repeat(MAX_PROVIDER_LABEL_LENGTH + 1)) ?? "", /At most/);
});

test("aliases: parse, set, clear and serialise", () => {
  const map = parseProviderAliases('{"shared":{"mesher":" Mesher "},"project:1":{"x":""},"junk":3}');
  assert.deepEqual(map, { shared: { mesher: "Mesher" } });
  assert.deepEqual(parseProviderAliases("not json"), {});

  const set = withProviderAlias(map, "project:1", "builder", "  Builder ");
  assert.deepEqual(set, { shared: { mesher: "Mesher" }, "project:1": { builder: "Builder" } });
  assert.deepEqual(map, { shared: { mesher: "Mesher" } }, "the input is not mutated");

  const cleared = withProviderAlias(set, "shared", "mesher", "   ");
  assert.deepEqual(cleared, { "project:1": { builder: "Builder" } });
  assert.deepEqual(parseProviderAliases(serialiseProviderAliases(cleared)), cleared);
});
