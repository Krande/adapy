import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  assetProviderCollections,
  collectionChoices,
  enabledFor,
  isCollectionEnabled,
  parseScopeCollections,
  serialiseScopeCollections,
  storedProviders,
  toggleCollection,
  withEnabled,
} from "../../services/assetScopeCollections";

test("parseScopeCollections reads the stored JSON text", () => {
  const map = parseScopeCollections('{"shared":{"vendor":["A","B"]}}');
  assert.deepEqual(map, { shared: { vendor: ["A", "B"] } });
});

test("parseScopeCollections accepts an already-decoded object", () => {
  // So a settings route that starts decoding for its callers breaks nobody.
  assert.deepEqual(parseScopeCollections({ shared: { vendor: ["A"] } }), { shared: { vendor: ["A"] } });
});

test("parseScopeCollections reads missing and malformed values as an empty map", () => {
  for (const raw of [null, undefined, "", "{not json", "[]", "42", '"text"', 7, ["a"]]) {
    assert.deepEqual(parseScopeCollections(raw), {}, `raw=${JSON.stringify(raw)}`);
  }
});

test("parseScopeCollections drops a provider entry that is not a list, leaving it unrestricted", () => {
  const map = parseScopeCollections({ shared: { good: ["A"], bad: "A", worse: { a: 1 } }, junk: 3 });
  assert.deepEqual(map, { shared: { good: ["A"] } });
  assert.equal(enabledFor(map, "shared", "bad"), null);
});

test("parseScopeCollections keeps only non-blank strings, once each", () => {
  const map = parseScopeCollections({ shared: { vendor: ["A", "", "  ", 3, null, "A", "B"] } });
  assert.deepEqual(map, { shared: { vendor: ["A", "B"] } });
});

test("enabledFor keeps missing (null) and empty ([]) distinct", () => {
  const map = parseScopeCollections({ shared: { vendor: [] }, "project:1": {} });
  assert.deepEqual(enabledFor(map, "shared", "vendor"), []);
  assert.equal(enabledFor(map, "shared", "other"), null);
  assert.equal(enabledFor(map, "project:1", "vendor"), null);
  assert.equal(enabledFor(map, "project:2", "vendor"), null);
});

test("isCollectionEnabled: unrestricted allows all, [] allows none, spelling is exact", () => {
  assert.equal(isCollectionEnabled(null, "ANY"), true);
  assert.equal(isCollectionEnabled([], "A"), false);
  assert.equal(isCollectionEnabled(["A"], "A"), true);
  // Core never translates: only the provider knows whether these are one thing.
  assert.equal(isCollectionEnabled(["A"], "a"), false);
});

test("withEnabled stores [] as [] and null as no entry", () => {
  const empty = withEnabled({}, "shared", "vendor", []);
  assert.deepEqual(empty, { shared: { vendor: [] } });
  assert.deepEqual(enabledFor(empty, "shared", "vendor"), []);

  const reset = withEnabled(empty, "shared", "vendor", null);
  // Not `{shared: {}}`: resetting the last restriction restores the untouched setting.
  assert.deepEqual(reset, {});
});

test("withEnabled leaves other scopes and providers alone, and does not mutate its input", () => {
  const before = parseScopeCollections({ shared: { a: ["X"], b: ["Y"] }, "project:1": { a: [] } });
  const snapshot = JSON.stringify(before);
  const after = withEnabled(before, "shared", "a", ["X", "Z", "X"]);
  assert.deepEqual(after, { shared: { a: ["X", "Z"], b: ["Y"] }, "project:1": { a: [] } });
  assert.equal(JSON.stringify(before), snapshot);
});

test("serialiseScopeCollections round-trips through the parser", () => {
  const map = withEnabled(withEnabled({}, "shared", "v", ["A"]), "project:1", "v", []);
  assert.deepEqual(parseScopeCollections(serialiseScopeCollections(map)), map);
});

test("assetProviderCollections reads the two declared keys off plugin specs", () => {
  const specs = [
    { slug: "plain", id: "plain" },
    { id: "exporter", title: "Exporter", asset_provider_id: "vendor", asset_collections_field: "projects", projects: ["B", "A"] },
  ];
  assert.deepEqual(assetProviderCollections(specs), [
    { providerId: "vendor", pluginIds: ["exporter"], titles: ["Exporter"], collections: ["A", "B"], refresh: null },
  ]);
});

test("assetProviderCollections carries a declared rescan, naming the plugin to run it on", () => {
  const specs = [
    { id: "one", asset_provider_id: "vendor", asset_collections_field: "items", items: ["A"] },
    {
      id: "two",
      asset_provider_id: "vendor",
      asset_collections_field: "items",
      items: ["B"],
      asset_collections_refresh: { action: "rescan" },
    },
    { id: "three", asset_provider_id: "other", asset_collections_field: "items", asset_collections_refresh: "rescan" },
  ];
  const [other, vendor] = assetProviderCollections(specs);
  assert.deepEqual(vendor.refresh, { pluginId: "two", options: { action: "rescan" } });
  // Only an object is a declaration: a bare string is not options core could send.
  assert.equal(other.refresh, null);
});

test("assetProviderCollections merges specs naming one provider", () => {
  const specs = [
    { id: "one", asset_provider_id: "vendor", asset_collections_field: "projects", projects: ["A", "B"] },
    { id: "two", asset_provider_id: "vendor", asset_collections_field: "items", items: ["B", "C"] },
  ];
  const [only] = assetProviderCollections(specs);
  assert.deepEqual(only.pluginIds, ["one", "two"]);
  assert.deepEqual(only.collections, ["A", "B", "C"]);
});

test("assetProviderCollections lists a declared provider with no usable list, empty", () => {
  const specs = [
    { id: "a", asset_provider_id: "vendor", asset_collections_field: "projects" },
    { id: "b", asset_provider_id: "other", asset_collections_field: "projects", projects: "A" },
    { id: "c", asset_provider_id: "", asset_collections_field: "projects", projects: ["A"] },
    null,
    "junk",
  ];
  assert.deepEqual(
    assetProviderCollections(specs).map((p) => [p.providerId, p.collections]),
    [["other", []], ["vendor", []]],
  );
});

test("collectionChoices keeps an enabled collection no online worker advertises", () => {
  // A worker being offline is not a revocation.
  const rows = collectionChoices(["A", "B"], ["B", "GONE"]);
  assert.deepEqual(rows, [
    { collection: "A", enabled: false, advertised: true },
    { collection: "B", enabled: true, advertised: true },
    { collection: "GONE", enabled: true, advertised: false },
  ]);
});

test("collectionChoices reads unrestricted as every advertised collection enabled", () => {
  assert.deepEqual(
    collectionChoices(["A"], null),
    [{ collection: "A", enabled: true, advertised: true }],
  );
});

test("toggleCollection never drops an unadvertised grant it was not asked about", () => {
  assert.deepEqual(toggleCollection(["GONE"], ["A"], "A", true), ["GONE", "A"]);
  assert.deepEqual(toggleCollection(["GONE", "A"], ["A"], "A", false), ["GONE"]);
});

test("toggleCollection from unrestricted switches off only the one collection", () => {
  assert.deepEqual(toggleCollection(null, ["A", "B", "C"], "B", false), ["A", "C"]);
});

test("the plugin API advertises the reader at 1.8.0, through the asset surface", async () => {
  // A plugin importing it into an older core fails from inside itself; the minor bump is what
  // turns that into the registry's one "built against x, this core is y" line.
  const { PLUGIN_API_VERSION, _versionSatisfies } = await import("@/plugins/registry");
  assert.ok(_versionSatisfies(PLUGIN_API_VERSION, ">=1.8.0"), PLUGIN_API_VERSION);
  const surface = await import("@/services/assets");
  assert.equal(surface.parseScopeCollections, parseScopeCollections);
  assert.equal(surface.enabledFor, enabledFor);
});

test("storedProviders lists every provider the map mentions", () => {
  const map = parseScopeCollections({ shared: { b: [], a: ["X"] }, "project:1": { c: [] } });
  assert.deepEqual(storedProviders(map), ["a", "b", "c"]);
});
