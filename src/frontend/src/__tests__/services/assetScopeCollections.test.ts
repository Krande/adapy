import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  assetProviderCollections,
  changeFeedSource,
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
    {
      providerId: "vendor",
      label: null,
      pluginIds: ["exporter"],
      titles: ["Exporter"],
      collections: ["A", "B"],
      refresh: null,
      request: null,
      nodeRequest: null,
      requestOptions: null,
      changeCheck: null,
      changeSource: null,
    },
  ]);
});

test("assetProviderCollections reads a declared provider label, string or per-provider map", () => {
  const specs = [
    { id: "a", asset_provider_id: "vendor", asset_collections_field: "c", c: [], asset_provider_label: "  Vendor\tlines " },
    { id: "a2", asset_provider_id: "vendor", asset_collections_field: "c", c: [], asset_provider_label: "Later" },
    { id: "b", asset_provider_id: "other", asset_collections_field: "c", c: [], asset_provider_label: { other: "Other one" } },
    { id: "c", asset_provider_id: "blank", asset_collections_field: "c", c: [], asset_provider_label: "   " },
    { id: "d", asset_provider_id: "wrong", asset_collections_field: "c", c: [], asset_provider_label: 3 },
  ];
  const byId = Object.fromEntries(assetProviderCollections(specs).map((p) => [p.providerId, p.label]));
  assert.deepEqual(byId, { blank: null, other: "Other one", vendor: "Vendor lines", wrong: null });
});

test("assetProviderCollections reads declared request options as the spec's own job_options", () => {
  const specs = [
    {
      id: "exporter",
      requires_admin: true,
      asset_provider_id: "vendor",
      asset_collections_field: "projects",
      projects: ["A"],
      job_options: [
        { name: "dbs", type: "string_list", title: "Extra DBs" },
        { name: "other", type: "bool" },
      ],
      asset_request_options: {
        options: ["dbs", "undeclared"],
        choices: { options: { action: "list" }, collection_option: "project", label: "List them" },
      },
    },
  ];
  const [p] = assetProviderCollections(specs);
  assert.equal(p.requestOptions?.pluginId, "exporter");
  // Only names the spec declares: "undeclared" has no type to render.
  assert.deepEqual(p.requestOptions?.decls.map((d) => d.name), ["dbs"]);
  assert.deepEqual(p.requestOptions?.choices, {
    pluginId: "exporter",
    options: { action: "list" },
    collectionOption: "project",
    label: "List them",
    requiresAdmin: true,
  });
});

test("request options naming nothing declared are no request options", () => {
  const [p] = assetProviderCollections([
    { id: "x", asset_provider_id: "vendor", asset_collections_field: "c", c: [], asset_request_options: { options: ["nope"] } },
  ]);
  assert.equal(p.requestOptions, null);
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

test("assetProviderCollections carries a declared request, and ignores one naming no option", () => {
  const specs = [
    {
      id: "one",
      asset_provider_id: "vendor",
      asset_collections_field: "items",
      requires_admin: true,
      asset_collection_request: { options: { action: "fetch" }, collection_option: "project", label: "Fetch" },
    },
    {
      id: "two",
      asset_provider_id: "other",
      asset_collections_field: "items",
      asset_collection_request: { options: { action: "fetch" } },
    },
  ];
  const [other, vendor] = assetProviderCollections(specs);
  assert.deepEqual(vendor.request, {
    pluginId: "one",
    options: { action: "fetch" },
    collectionOption: "project",
    label: "Fetch",
    requiresAdmin: true,
  });
  assert.equal(other.request, null);
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

test("a spec's asset_node_request is read with the option that carries the node", () => {
  const [vendor] = assetProviderCollections([
    {
      id: "one",
      asset_provider_id: "vendor",
      asset_collections_field: "items",
      asset_node_request: { options: { action: "export" }, collection_option: "project", node_option: "nodes", label: "Request geometry" },
    },
  ]);
  assert.deepEqual(vendor.nodeRequest, {
    pluginId: "one",
    options: { action: "export" },
    collectionOption: "project",
    nodeOption: "nodes",
    label: "Request geometry",
    requiresAdmin: false,
  });
  // Without a node option it is not a node request: the node would have nowhere to go.
  const [none] = assetProviderCollections([
    { id: "two", asset_provider_id: "other", asset_node_request: { options: {}, collection_option: "project" } },
  ]);
  assert.equal(none.nodeRequest, null);
});

test("a node request's max_nodes is read as declared; anything that is not a count above 1 is absent", () => {
  const read = (max_nodes: unknown) =>
    assetProviderCollections([
      {
        id: "one",
        asset_provider_id: "vendor",
        asset_collections_field: "items",
        asset_node_request: { options: {}, collection_option: "project", node_option: "nodes", max_nodes },
      },
    ])[0].nodeRequest?.maxNodes;
  assert.equal(read(20), 20);
  for (const bad of [1, 0, -3, 2.5, "20", null, undefined]) assert.equal(read(bad), undefined, String(bad));
});
test("on_demand is read only when declared true -- a load may then request the node first", () => {
  const specs = (on_demand?: unknown) => [
    {
      id: "p",
      asset_provider_id: "quick",
      asset_node_request: { options: {}, collection_option: "project", node_option: "nodes", ...(on_demand === undefined ? {} : { on_demand }) },
    },
  ];
  assert.equal(assetProviderCollections(specs(true))[0].nodeRequest?.onDemand, true);
  assert.equal(assetProviderCollections(specs())[0].nodeRequest?.onDemand, undefined);
  assert.equal(assetProviderCollections(specs("yes"))[0].nodeRequest?.onDemand, undefined, "only a real true opts in");
});

test("assetProviderCollections reads the change-check entry of asset_schedules", () => {
  const [p] = assetProviderCollections([
    {
      id: "vendor-plugin",
      asset_provider_id: "vendor",
      asset_collections_field: "projects",
      projects: ["ALPHA"],
      requires_admin: true,
      asset_schedules: [
        { id: "sweep", kind: "job", options: { action: "sweep" }, collection_option: "project" },
        { id: "changes", kind: "change-check", label: "Changes", options: { action: "check" }, collection_option: "project" },
      ],
    },
  ]);
  assert.deepEqual(p.changeCheck, {
    pluginId: "vendor-plugin",
    id: "changes",
    label: "Changes",
    description: null,
    requiresAdmin: true,
  });
});

test("assetProviderCollections ignores a change check it could not run", () => {
  for (const asset_schedules of [
    undefined,
    "nope",
    [{ id: "c", kind: "change-check", options: { action: "x" } }], // no collection_option
    [{ kind: "change-check", options: {}, collection_option: "p" }], // no id
    [{ id: "j", kind: "job", options: {}, collection_option: "p" }], // not a check
  ]) {
    const [p] = assetProviderCollections([{ id: "x", asset_provider_id: "vendor", asset_schedules }]);
    assert.equal(p.changeCheck, null, JSON.stringify(asset_schedules));
  }
});

test("changeFeedSource reads a provider's per-collection feed source, else the provider id", () => {
  assert.equal(changeFeedSource(null, "vendor", "alpha"), "vendor");
  assert.equal(changeFeedSource("  ", "vendor", "alpha"), "vendor");
  assert.equal(changeFeedSource("vendor:{COLLECTION}", "vendor", "alpha"), "vendor:ALPHA");
  assert.equal(changeFeedSource("feed/{collection}", "vendor", "Alpha"), "feed/Alpha");
  // A template needs a collection; without one the provider id is the honest fallback.
  assert.equal(changeFeedSource("vendor:{COLLECTION}", "vendor", null), "vendor");
  assert.equal(changeFeedSource("fixed-feed", "vendor", null), "fixed-feed");
});

test("assetProviderCollections reads asset_change_source", () => {
  const [p] = assetProviderCollections([
    { id: "x", asset_provider_id: "vendor", asset_change_source: "vendor:{COLLECTION}" },
  ]);
  assert.equal(p.changeSource, "vendor:{COLLECTION}");
});