import assert from "node:assert/strict";
import test from "node:test";

import {
  buildQuery,
  emptyForm,
  hitKey,
  hitTreePath,
  mergePage,
  parseStoredForm,
  partitionCounts,
  shortName,
  totalCount,
  treeCollectionFor,
  walkHitPath,
  type FilterRow,
  type SearchForm,
} from "../../assets/attributeSearch";
import type { PickTreeSource } from "../../assets/scenePick";
import type { SearchHit, SearchResponse } from "../../services/api/attributeSearch";

const row = (key: string, op: FilterRow["op"], value: string, typed?: FilterRow["typed"]): FilterRow => ({
  id: `${key}-${op}`,
  key,
  op,
  value,
  ...(typed ? { typed } : {}),
});

const form = (patch: Partial<SearchForm>): SearchForm => ({ ...emptyForm(), ...patch });

test("filter rows become where clauses; blank rows are skipped, empty values refused", () => {
  const built = buildQuery(
    form({
      rows: [row("FUNC", "eq", " MS "), row("PURP", "in", "NOD, SUPP,,NOD"), row("DESC", "prefix", "ST"), row("", "eq", "x")],
    }),
  );
  assert.ok(built.ok);
  assert.deepEqual(built.query.where, [
    { key: "FUNC", eq: "MS" },
    { key: "PURP", in: ["NOD", "SUPP"] },
    { key: "DESC", prefix: "ST" },
  ]);
  assert.equal(built.query.collections, null);
  assert.equal(built.query.providers, null);
  assert.equal(built.query.kind, null);
  assert.equal(built.query.depth, null);
  assert.equal(built.query.select, null);
  assert.equal(built.query.cursor, null);
  assert.equal(built.query.limit, 200);

  const missing = buildQuery(form({ rows: [row("FUNC", "eq", "  ")] }));
  assert.equal(missing.ok, false);
});

test("a value picked from the suggestions is sent as the type the index holds", () => {
  const built = buildQuery(
    form({ rows: [row("NUM", "eq", "12", { "12": 12 }), row("FLAG", "in", "true, x", { true: true }), row("TXT", "eq", "12")] }),
  );
  assert.ok(built.ok);
  assert.deepEqual(built.query.where, [
    { key: "NUM", eq: 12 },
    { key: "FLAG", in: [true, "x"] },
    { key: "TXT", eq: "12" },
  ]);
});

test("scope filters, kinds, depth, columns and the cursor reach the query", () => {
  const built = buildQuery(
    form({
      collections: ["ALPHA"],
      providers: ["prov-a"],
      kind: "PIPE, EQUI",
      depthMode: "range",
      depthMin: "2",
      depthMax: "",
      columns: ["DESC"],
      limit: 5000,
    }),
    "c1",
  );
  assert.ok(built.ok);
  assert.deepEqual(built.query.collections, ["ALPHA"]);
  assert.deepEqual(built.query.providers, ["prov-a"]);
  assert.deepEqual(built.query.kind, ["PIPE", "EQUI"]);
  assert.deepEqual(built.query.depth, { min: 2 });
  assert.deepEqual(built.query.select, ["DESC"]);
  assert.equal(built.query.limit, 1000);
  assert.equal(built.query.cursor, "c1");

  const eq = buildQuery(form({ depthMode: "eq", depthEq: "3" }));
  assert.ok(eq.ok);
  assert.deepEqual(eq.query.depth, { eq: 3 });
  assert.equal(buildQuery(form({ depthMode: "eq", depthEq: "2.5" })).ok, false);
  assert.equal(buildQuery(form({ depthMode: "range", depthMin: "4", depthMax: "1" })).ok, false);
  const open = buildQuery(form({ depthMode: "range" }));
  assert.ok(open.ok);
  assert.equal(open.query.depth, null);
});

const hit = (id: string, extra: Partial<SearchHit> = {}): SearchHit => ({
  provider: "prov-a",
  collection: "ALPHA",
  node_id: id,
  parent_id: null,
  full_name: `/${id}`,
  source_ref: null,
  kind: "K",
  depth: 1,
  attrs: {},
  path: [],
  ...extra,
});

const page = (ids: string[], next: string | null, counts: Record<string, number>, took = 5): SearchResponse => ({
  rows: ids.map((i) => hit(i)),
  next_cursor: next,
  counts,
  took_ms: took,
});

test("pages fold in order, without duplicates, with the latest counts and cursor", () => {
  const first = mergePage(null, page(["a", "b"], "c1", { "prov-a/ALPHA": 3 }, 7));
  assert.deepEqual(first.hits.map((h) => h.node_id), ["a", "b"]);
  assert.equal(first.nextCursor, "c1");
  const second = mergePage(first, page(["b", "c"], null, { "prov-a/ALPHA": 3 }, 4));
  assert.deepEqual(second.hits.map((h) => h.node_id), ["a", "b", "c"]);
  assert.equal(second.nextCursor, null);
  assert.equal(second.tookMs, 4);
  assert.equal(second.totalTookMs, 11);
  assert.equal(second.pages, 2);
  // The same node id in another collection is another hit.
  assert.notEqual(hitKey(hit("a")), hitKey(hit("a", { collection: "BETA" })));
});

test("partition counts split on the first slash and sum", () => {
  const counts = { "prov-a/ALPHA": 2, "prov-b/BETA/X": 5 };
  assert.deepEqual(partitionCounts(counts), [
    { provider: "prov-b", collection: "BETA/X", count: 5 },
    { provider: "prov-a", collection: "ALPHA", count: 2 },
  ]);
  assert.equal(totalCount(counts), 7);
});

test("a hit's collection maps onto the tree's, exact first then ignoring case", () => {
  assert.equal(treeCollectionFor(["alpha", "beta"], "ALPHA"), "alpha");
  assert.equal(treeCollectionFor(["ALPHA", "alpha"], "alpha"), "alpha");
  assert.equal(treeCollectionFor(["beta"], "ALPHA"), null);
  assert.equal(treeCollectionFor(null, "ALPHA"), null);
});

test("a hit's tree path ends at the hit, once", () => {
  const p = [{ node_id: "s", full_name: "/S", kind: "SITE", depth: 1 }];
  assert.deepEqual(hitTreePath(hit("n", { path: p })).map((e) => e.node_id), ["s", "n"]);
  assert.deepEqual(
    hitTreePath(hit("n", { path: [...p, { node_id: "n", full_name: "/n", kind: "K", depth: 2 }] })).map((e) => e.node_id),
    ["s", "n"],
  );
  assert.equal(shortName("/SITE-A/ZONE 1/"), "ZONE 1");
});

/** A canned lazy tree: `levels` are revealed by `ensureChildren`. */
function lazyTree(initial: Record<string, string>, levels: Record<string, Record<string, string>>) {
  const labels = new Map(Object.entries(initial));
  const kids = new Map<string, string[]>();
  const fetched: string[] = [];
  const src: PickTreeSource = {
    label: (id) => labels.get(id),
    children: (id) => kids.get(id) ?? [],
    ensureChildren: async (id) => {
      if (fetched.includes(id) || !levels[id]) return;
      fetched.push(id);
      for (const [c, l] of Object.entries(levels[id])) labels.set(c, l);
      kids.set(id, Object.keys(levels[id]));
    },
  };
  return { src, fetched };
}

test("the walk opens levels down the path by id and stops at the hit", async () => {
  const { src, fetched } = lazyTree({ s: "SITE-A" }, { s: { z: "ZONE-1" }, z: { n: "PIPE-9" } });
  const path = [
    { node_id: "w", full_name: "/", kind: "WORLD", depth: 0 }, // above the tree's top: skipped
    { node_id: "s", full_name: "/SITE-A", kind: "SITE", depth: 1 },
    { node_id: "z", full_name: "/ZONE-1", kind: "ZONE", depth: 2 },
    { node_id: "n", full_name: "/PIPE-9", kind: "PIPE", depth: 3 },
  ];
  const out = await walkHitPath(src, path);
  assert.deepEqual(out, { row: "n", chain: ["s", "z", "n"], exact: true });
  assert.deepEqual(fetched, ["s", "z"]);
});

test("a level drawn under another id is matched by name; a missing hit reveals its nearest ancestor", async () => {
  const { src } = lazyTree({ s: "SITE-A" }, { s: { other: "ZONE-1" }, other: { n2: "PIPE-8" } });
  const path = [
    { node_id: "s", full_name: "/SITE-A", kind: "SITE", depth: 1 },
    { node_id: "z", full_name: "/ZONE-1", kind: "ZONE", depth: 2 },
    { node_id: "n", full_name: "/PIPE-9", kind: "PIPE", depth: 3 },
  ];
  const out = await walkHitPath(src, path);
  assert.deepEqual(out, { row: "other", chain: ["s", "other"], exact: false });

  const none = await walkHitPath(lazyTree({}, {}).src, path);
  assert.equal(none, null);
});

test("a stored form is read back defensively", () => {
  assert.equal(parseStoredForm(null), null);
  assert.equal(parseStoredForm("{nope"), null);
  const back = parseStoredForm(
    JSON.stringify({
      collections: ["ALPHA", 3],
      kind: "PIPE",
      depthMode: "eq",
      depthEq: "2",
      rows: [{ key: "FUNC", op: "in", value: "A,B", typed: { A: 1, B: { bad: 1 } } }, null],
      columns: ["DESC"],
      limit: 50,
    }),
  );
  assert.ok(back);
  assert.deepEqual(back.collections, ["ALPHA"]);
  assert.equal(back.depthMode, "eq");
  assert.equal(back.rows.length, 1);
  assert.equal(back.rows[0].op, "in");
  assert.deepEqual(back.rows[0].typed, { A: 1 });
  assert.equal(back.limit, 50);
  const built = buildQuery(back);
  assert.ok(built.ok);
  assert.deepEqual(built.query.where, [{ key: "FUNC", in: [1, "B"] }]);
});
