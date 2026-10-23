import assert from "node:assert/strict";
import test from "node:test";

import {
  TREE_SERVER_SEARCH_LIMIT,
  hitPathText,
  serverSearchNote,
  treeSearchPartitions,
  treeServerQuery,
  unloadedHits,
} from "../../assets/treeServerSearch";
import { compactQuery } from "../../assets/searchExport";
import type { SearchCollection, SearchHit } from "../../services/api/attributeSearch";

const part = (provider: string, collection: string, extra: Partial<SearchCollection> = {}): SearchCollection => ({
  provider,
  collection,
  revision: null,
  rows: 1,
  source_etag: null,
  ingested_at: null,
  ...extra,
});

const hit = (node_id: string, provider = "prov-a", patch: Partial<SearchHit> = {}): SearchHit => ({
  provider,
  collection: "aaa",
  node_id,
  parent_id: null,
  full_name: `/${node_id}`,
  source_ref: null,
  kind: null,
  depth: 1,
  attrs: {},
  path: [],
  ...patch,
});

test("the tree's partitions: every provider's own partition of its collection, any case, never a shared one", () => {
  const listed = [
    part("prov-b", "aaa"),
    part("prov-a", "AAA"),
    part("prov-a", "bbb"),
    part("prov-c", "aaa", { key: "prov-c/aaa@shared", scope: "shared", shared: true }),
  ];
  assert.deepEqual(treeSearchPartitions(listed, "aaa"), ["prov-a/AAA", "prov-b/aaa"]);
  assert.deepEqual(treeSearchPartitions(listed, "AAA"), ["prov-a/AAA", "prov-b/aaa"]);
  assert.deepEqual(treeSearchPartitions(listed, "ccc"), []);
  assert.deepEqual(treeSearchPartitions(listed, null), []);
  // a listing that names its keys is taken at its word
  assert.deepEqual(treeSearchPartitions([part("p", "aaa", { key: "p/aaa" }), part("p", "aaa", { key: "p/aaa" })], "aaa"), ["p/aaa"]);
});

test("the server query: name or ref contains the term, any case, over the named partitions", () => {
  const q = treeServerQuery("  pipe-1 ", ["prov-a/aaa"]);
  assert.ok(q);
  assert.deepEqual(q.name, { contains: "pipe-1", ignore_case: true, refs: true });
  assert.deepEqual(q.partitions, ["prov-a/aaa"]);
  assert.equal(q.limit, TREE_SERVER_SEARCH_LIMIT);
  assert.deepEqual(q.where, []);
  assert.equal(q.cursor, null);
  assert.equal(treeServerQuery("  ", ["prov-a/aaa"]), null);
  assert.equal(treeServerQuery("pipe", []), null); // no index for the collection: nothing to ask
  assert.equal(compactQuery(q), "name contains pipe-1");
});

test("unloaded hits: once each, in order, without the rows the tree holds", () => {
  const hits = [hit("n1"), hit("n2"), hit("n1", "prov-b"), hit("n3")];
  const held = new Set(["n2"]);
  assert.deepEqual(
    unloadedHits(hits, (id) => held.has(id)).map((h) => `${h.provider}:${h.node_id}`),
    ["prov-a:n1", "prov-a:n3"],
  );
  assert.deepEqual(unloadedHits([], () => false), []);
});

test("a hit's path reads root first, ending at the hit", () => {
  const h = hit("n5", "prov-a", {
    full_name: "/ATTA-1",
    path: [
      { node_id: "s", full_name: "/SITE-A", kind: "SITE", depth: 1 },
      { node_id: "p", full_name: "/PIPE-1", kind: "PIPE", depth: 2 },
    ],
  });
  assert.equal(hitPathText(h), "/SITE-A › /PIPE-1 › /ATTA-1");
  assert.equal(hitPathText(hit("n6")), "/n6");
});

test("a failed server search is one quiet line, a timeout said as one", () => {
  assert.match(serverSearchNote(new Error("searchAssetAttributes: 504 the search did not finish")), /timed out/);
  assert.match(serverSearchNote(new Error("searchAssetAttributes: 503")), /unavailable/);
  assert.match(serverSearchNote("boom"), /unavailable/);
});
