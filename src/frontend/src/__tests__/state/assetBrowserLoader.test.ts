/**
 * The Assets tab's fetch side, driven against canned wire documents.
 *
 * Pins: the collection opened is the one with the newest publish; the tops of
 * the tree are the UNION of every collection index the mode admits, merged
 * oldest first so the newest word about a shared node wins even when an older
 * index arrives late; a spine is opened ONE LEVEL at a time, each level fetched
 * once per (subject, revision, node); and a response for a collection the user
 * has since left is dropped.
 */

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type { SourceNodesAnswer } from "../../assets/changes";
import { levelKey } from "../../assets/spines";
import type { WireAssetIndex, WireHierarchySlice } from "../../assets/types";
import { useAssetBrowserStore } from "../../state/assetBrowserStore";
import { createAssetBrowserLoader, type AssetsApiLike, type SourceNodesApiLike } from "../../state/assetBrowserLoader";

const SCOPE = "user:me";
const R1 = "20260901T100000Z";
const R2 = "20260902T100000Z";
const R3 = "20260903T100000Z";

const COLS = ["id", "parent", "label", "kind", "leaf", "delivery"];

function slice(root: string | null, rows: unknown[][], collection = "plant-a"): WireHierarchySlice {
  return {
    schema: "ada.assets/hierarchy@1",
    provider: "fixture-lines",
    collection,
    root,
    produced_at: "2026-09-01T10:00:00Z",
    depth: root ? 9 : 1,
    cols: COLS,
    rows,
  };
}

/** One level of a spine, as the route's `parent=` answers it: a trailing `children` column. */
function level(root: string, rows: unknown[][]): WireHierarchySlice {
  return { ...slice(root, rows), depth: 1, cols: [...COLS, "children"] };
}

const rev = (revision: string, files: string[], delivery = "none") => ({
  revision,
  files,
  manifest: { provider: "fixture-lines", delivery: delivery as "none", produced_at: "x" },
});

/** plant-a: two collection indexes (R1 lists area-1, R2 relabels it and adds
 *  area-2); area-1 has its own subtree spine at R1. other: an older collection. */
function makeApi() {
  const calls: string[] = [];
  const indexFor: Record<string, WireAssetIndex> = {
    "*": {
      collections: {
        "plant-a": [{ subject: "plant-a", revisions: [{ revision: R2, files: [] }] }],
        "old-b": [{ subject: "old-b", revisions: [{ revision: R1, files: [] }] }],
      },
      malformed: [],
    },
    "plant-a": {
      collections: {
        "plant-a": [
          {
            subject: "plant-a",
            // newest first, as the server sends it
            revisions: [
              rev(R2, ["asset.json", "hierarchy.json"]),
              rev(R1, ["asset.json", "hierarchy.json"]),
            ],
          },
          { subject: "area-1", revisions: [rev(R1, ["asset.json", "hierarchy.json"], "build")] },
        ],
      },
      malformed: [],
    },
    "old-b": { collections: { "old-b": [] }, malformed: [] },
  };
  const trees: Record<string, WireHierarchySlice> = {
    [`plant-a|index|${R1}`]: slice(null, [
      ["area-1", null, "Area One (old label)", "area", 0, ""],
    ]),
    [`plant-a|index|${R2}`]: slice(null, [
      ["area-1", null, "Area One", "area", 0, ""],
      ["area-2", null, "Area Two", "area", 0, ""],
    ]),
    // area-1's spine, ONE LEVEL at a time (`parent=`): each row counts its own
    // children in the spine.
    [`plant-a|area-1|${R1}|area-1`]: level("area-1", [["level-1", "area-1", "Level 1", "level", 0, "", 1]]),
    [`plant-a|area-1|${R1}|level-1`]: level("area-1", [["member-1", "level-1", "Member 1", "member", 1, "build", 0]]),
  };
  const gates = new Map<string, () => void>();
  const api: AssetsApiLike = {
    async getAssetIndex(_scope, collection) {
      calls.push(`index:${collection ?? "*"}`);
      return indexFor[collection ?? "*"];
    },
    async getAssetTree(_scope, _provider, collection, opts) {
      const key = `${collection}|${opts.root ?? "index"}|${opts.revision}${opts.parent ? `|${opts.parent}` : ""}`;
      calls.push(`tree:${key}`);
      const gate = gates.get(key);
      if (gate) await new Promise<void>((resolve) => gates.set(key, resolve));
      const doc = trees[key];
      if (!doc) throw new Error(`no tree ${key}`);
      return doc;
    },
  };
  return { api, calls, gates };
}

beforeEach(() => {
  useAssetBrowserStore.getState().resetForScope(SCOPE);
  useAssetBrowserStore.getState().setMode({ kind: "latest" });
});

test("opens the collection with the newest publish, and unions its indexes oldest first", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  const s = useAssetBrowserStore.getState();
  assert.deepEqual(s.collections, ["old-b", "plant-a"]);
  assert.equal(s.collection, "plant-a"); // newest revision wins, not alphabetical
  assert.deepEqual(s.mergedIndexRevisions, [R1, R2]);
  // R2 merged after R1: its label wins for the shared node, and area-2 is added.
  assert.equal(s.forest.nodes.get("area-1")?.label, "Area One");
  assert.ok(s.forest.nodes.has("area-2"));
  assert.deepEqual(s.forest.origins.get("area-1"), { subject: "plant-a", revision: R2 });
});

test("run mode narrows the admitted indexes without refetching them", async () => {
  const { api, calls } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  const before = calls.length;
  useAssetBrowserStore.getState().setMode({ kind: "run", revision: R1 });
  await loader.syncCollectionIndexes(SCOPE);
  assert.deepEqual(useAssetBrowserStore.getState().mergedIndexRevisions, [R1]);
  assert.equal(calls.length, before, "nothing new to fetch");
});

test("a late older index does not overwrite a newer one's rows", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  // Start coeval on R2 only, then widen to latest -- R1 arrives after R2.
  useAssetBrowserStore.getState().setMode({ kind: "run", revision: R2 });
  await loader.loadCollections(SCOPE);
  assert.deepEqual(useAssetBrowserStore.getState().mergedIndexRevisions, [R2]);
  useAssetBrowserStore.getState().setMode({ kind: "latest" });
  await loader.syncCollectionIndexes(SCOPE);
  const s = useAssetBrowserStore.getState();
  assert.deepEqual(s.mergedIndexRevisions, [R1, R2]);
  assert.equal(s.forest.nodes.get("area-1")?.label, "Area One", "R2 re-merged after the late R1");
});

/** The fixture's whole-scope index, writable: what a publish (or a delete) changes server-side. */
const publishedCollections = (index: WireAssetIndex) =>
  index.collections as Record<string, WireAssetIndex["collections"][string]>;

test("a refresh re-reads which collections exist -- a publish can be a new one's first", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  publishedCollections(await api.getAssetIndex(SCOPE))["new-c"] = [{ subject: "new-c", revisions: [{ revision: R3, files: [] }] }];
  await loader.refresh(SCOPE);
  const s = useAssetBrowserStore.getState();
  assert.deepEqual(s.collections, ["new-c", "old-b", "plant-a"]);
  assert.equal(s.collection, "plant-a", "the open collection stays open");
  assert.ok(s.forest.nodes.has("area-2"), "and is rebuilt");
});

test("a refresh whose open collection is gone opens another, as a first load would", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  delete publishedCollections(await api.getAssetIndex(SCOPE))["plant-a"];
  await loader.refresh(SCOPE);
  const s = useAssetBrowserStore.getState();
  assert.deepEqual(s.collections, ["old-b"]);
  assert.equal(s.collection, "old-b");
});

const AREA_FIRST = { subject: "area-1", revision: R1, node: "area-1" };
const LEVEL_1 = { subject: "area-1", revision: R1, node: "level-1" };

test("a level is fetched once per (subject, revision, node), with root/revision/parent, never the whole spine", async () => {
  const { api, calls } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  await loader.loadLevel(SCOPE, AREA_FIRST);
  await loader.loadLevel(SCOPE, AREA_FIRST);
  const trees = calls.filter((c) => c.startsWith("tree:plant-a|area-1|"));
  assert.deepEqual(trees, [`tree:plant-a|area-1|${R1}|area-1`], "one call, for one level");
  const s = useAssetBrowserStore.getState();
  assert.ok(s.levelLoaded.has(levelKey(AREA_FIRST)));
  assert.ok(!s.levelLoaded.has(levelKey(LEVEL_1)), "nothing deeper is prefetched");
  assert.equal(s.forest.nodes.get("level-1")?.parent, "area-1");
  assert.equal(s.forest.nodes.get("level-1")?.children, 1, "the count is what lets it expand next");
  assert.ok(!s.forest.nodes.has("member-1"));
  // Merged under the origin the whole spine would have given it.
  assert.deepEqual(s.forest.origins.get("level-1"), { subject: "area-1", revision: R1 });
});

test("successive levels of one spine union, each under the spine's origin", async () => {
  const { api, calls } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  await loader.loadLevel(SCOPE, AREA_FIRST);
  await loader.loadLevel(SCOPE, LEVEL_1);
  assert.ok(calls.includes(`tree:plant-a|area-1|${R1}|level-1`), "root = the owning subject, parent = the row");
  const s = useAssetBrowserStore.getState();
  assert.ok(s.forest.nodes.has("level-1"), "the first level survives the second");
  assert.equal(s.forest.nodes.get("member-1")?.parent, "level-1");
  assert.deepEqual(s.forest.origins.get("member-1"), { subject: "area-1", revision: R1 });
  assert.ok(s.levelLoaded.has(levelKey(LEVEL_1)));
});

test("a level is marked loading while in flight, per node", async () => {
  const { api, gates } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  const key = `plant-a|area-1|${R1}|area-1`;
  gates.set(key, () => {});
  const pending = loader.loadLevel(SCOPE, AREA_FIRST);
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(useAssetBrowserStore.getState().levelLoading.has(levelKey(AREA_FIRST)));
  assert.ok(!useAssetBrowserStore.getState().levelLoading.has(levelKey(LEVEL_1)));
  gates.get(key)!();
  await pending;
  const s = useAssetBrowserStore.getState();
  assert.ok(!s.levelLoading.has(levelKey(AREA_FIRST)));
  assert.ok(s.levelLoaded.has(levelKey(AREA_FIRST)));
});

test("a failed level is recorded against its (subject, revision, node) and retried explicitly", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  const req = { subject: "area-2", revision: R2, node: "area-2" };
  await loader.loadLevel(SCOPE, req);
  const s = useAssetBrowserStore.getState();
  assert.match(s.levelErrors.get(levelKey(req)) ?? "", /no tree/);
  assert.ok(!s.levelLoading.has(levelKey(req)));
  assert.ok(!s.levelLoaded.has(levelKey(req)));
});

test("a level whose row was re-drawn from another revision meanwhile is dropped, not merged", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  await loader.loadLevel(SCOPE, AREA_FIRST);
  // area-1's rows are now from R2 (its new first level retired the R1 ones)...
  useAssetBrowserStore.getState().mergeLevelSlice(
    [{ id: "level-1", parent: "area-1", label: "Level 1", kind: "level", leaf: false, delivery: "none", provider: "fixture-lines", children: 1 }],
    { subject: "area-1", revision: R2, parent: "area-1" },
  );
  // ...so an R1 answer about level-1 belongs to a tree no longer on screen.
  await loader.loadLevel(SCOPE, LEVEL_1);
  const s = useAssetBrowserStore.getState();
  assert.ok(!s.forest.nodes.has("member-1"));
  assert.deepEqual(s.forest.origins.get("level-1"), { subject: "area-1", revision: R2 }, "the new revision's rows survive");
});

test("loadLevels opens one level per request -- what `place` does", async () => {
  const { api, calls } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  await loader.loadLevels(SCOPE, [AREA_FIRST]);
  assert.deepEqual(
    calls.filter((c) => c.includes("|area-1|")),
    [`tree:plant-a|area-1|${R1}|area-1`],
  );
});

// ---------------------------------------------------------------------------
// the change feed's fetch side (§Decision 4, Phase 4): eager per published
// root, lazy per spine, best-effort, and optional (a caller with no
// `sourceNodesApi` -- every test above this point -- gets a pure no-op).
// ---------------------------------------------------------------------------

function makeSourceNodesApi(answerFor: (source: string, refs: readonly string[]) => SourceNodesAnswer | null) {
  const calls: { source: string; refs: readonly string[] }[] = [];
  const api: SourceNodesApiLike = {
    async getSourceNodes(_scope, source, refs) {
      calls.push({ source, refs: [...refs] });
      return answerFor(source, refs);
    },
  };
  return { api, calls };
}

test("root evidence is fetched eagerly for every published subject (not the collection itself), grouped by provider", async () => {
  const { api } = makeApi();
  const { api: sourceApi, calls } = makeSourceNodesApi((source, refs) => ({
    source,
    rows: new Map(refs.map((r) => [r, { nodeRef: r, parentRef: null, name: null, lastChangedAt: "2026-08-01T00:00:00Z", lastChangedBy: null, observedAt: "2026-08-01T00:00:00Z", action: null }])),
    unknown: new Set(),
  }));
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api, sourceApi);
  await loader.loadCollections(SCOPE);
  // area-1 is the only published subject other than the collection index
  // itself ("plant-a"); the collection subject is never asked about as an
  // export root.
  assert.equal(calls.length, 1);
  assert.equal(calls[0].source, "fixture-lines");
  assert.deepEqual(calls[0].refs, ["area-1"]);
  const s = useAssetBrowserStore.getState();
  assert.ok(s.evidenceAsked.has("area-1"));
  assert.ok(!s.evidenceAsked.has("plant-a"), "the collection index is not an export root");
});

test("per-level evidence asks only the refs THIS level brought in, deduped against what root evidence already asked", async () => {
  const { api } = makeApi();
  const { api: sourceApi, calls } = makeSourceNodesApi((source, refs) => ({
    source,
    rows: new Map(),
    unknown: new Set(refs),
  }));
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api, sourceApi);
  await loader.loadCollections(SCOPE); // asks about area-1 eagerly (root evidence)
  calls.length = 0;
  await loader.loadLevel(SCOPE, AREA_FIRST);
  assert.equal(calls.length, 1);
  // area-1 itself was already asked by root evidence -- the level's own refs
  // (the row that asked plus every node id the level brought in) are deduped
  // against the GLOBAL `evidenceAsked` set, not re-requested.
  assert.deepEqual([...calls[0].refs].sort(), ["level-1"]);
  await loader.loadLevel(SCOPE, LEVEL_1);
  assert.deepEqual([...calls[1].refs].sort(), ["member-1"]);
  const s = useAssetBrowserStore.getState();
  assert.ok(s.evidenceAsked.has("level-1"));
  assert.ok(s.evidenceAsked.has("member-1"));
});

test("a second load of the same level does not re-ask evidence for refs it already has", async () => {
  const { api } = makeApi();
  const { api: sourceApi, calls } = makeSourceNodesApi((source, refs) => ({ source, rows: new Map(), unknown: new Set(refs) }));
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api, sourceApi);
  await loader.loadCollections(SCOPE);
  await loader.loadLevel(SCOPE, AREA_FIRST);
  const before = calls.length;
  await loader.loadLevel(SCOPE, AREA_FIRST); // idempotent: `levelLoaded` already has it
  assert.equal(calls.length, before, "the level itself is not re-fetched, so evidence is not re-asked either");
});

test("the feed answering no-feed (null) is recorded as such, not silently dropped", async () => {
  const { api } = makeApi();
  const { api: sourceApi } = makeSourceNodesApi(() => null);
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api, sourceApi);
  await loader.loadCollections(SCOPE);
  const s = useAssetBrowserStore.getState();
  assert.equal(s.sourceAnswer.get("fixture-lines"), null);
  assert.ok(s.evidenceAsked.has("area-1"), "asked and told no-feed is still having asked");
});

test("a caller that supplies no sourceNodesApi gets a pure no-op -- evidence fetching is optional plumbing", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api); // two-arg call, exactly like every earlier test in this file
  await loader.loadCollections(SCOPE);
  await loader.loadLevel(SCOPE, AREA_FIRST);
  await loader.loadLevel(SCOPE, LEVEL_1);
  const s = useAssetBrowserStore.getState();
  assert.equal(s.evidenceAsked.size, 0);
  assert.equal(s.sourceAnswer.size, 0);
  // The hierarchy work itself is unaffected either way.
  assert.equal(s.forest.nodes.get("member-1")?.parent, "level-1");
});

test("a response for a collection the user has left is dropped", async () => {
  const { api, gates } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  const key = `plant-a|area-1|${R1}|area-1`;
  gates.set(key, () => {});
  const pending = loader.loadLevel(SCOPE, AREA_FIRST);
  await new Promise((r) => setTimeout(r, 0));
  await loader.chooseCollection(SCOPE, "old-b");
  gates.get(key)!();
  await pending;
  const s = useAssetBrowserStore.getState();
  assert.equal(s.collection, "old-b");
  assert.ok(!s.forest.nodes.has("level-1"), "the late plant-a level did not land in old-b's forest");
});

test("the geometry roll-up is read with the index, re-read on refresh, and optional", async () => {
  const { api } = makeApi();
  let served = 0;
  const withRollup: AssetsApiLike = {
    ...api,
    async getGeometryRollup(_scope, collection) {
      served++;
      return {
        schema: "ada.assets/geometry@1",
        collection,
        index_token: `t${served}`,
        providers: {},
        any: { here: ["member-1"], below: ["area-1", "level-1"] },
      };
    },
  };
  const loader = createAssetBrowserLoader(useAssetBrowserStore, withRollup);
  await loader.loadCollections(SCOPE);
  assert.equal(useAssetBrowserStore.getState().geometryRollup?.index_token, "t1");
  await loader.refresh(SCOPE);
  assert.equal(useAssetBrowserStore.getState().geometryRollup?.index_token, "t2");
  await loader.chooseCollection(SCOPE, "old-b");
  assert.equal(useAssetBrowserStore.getState().geometryRollup?.collection, "old-b");

  // A server without the route: the tree loads as before, and the overlay stays client-only.
  useAssetBrowserStore.getState().resetForScope(SCOPE);
  const failing: AssetsApiLike = {
    ...api,
    async getGeometryRollup() {
      throw new Error("404 Not Found");
    },
  };
  await createAssetBrowserLoader(useAssetBrowserStore, failing).loadCollections(SCOPE);
  const s = useAssetBrowserStore.getState();
  assert.equal(s.geometryRollup, null);
  assert.equal(s.indexError, null);
  assert.ok(s.forest.nodes.has("area-1"));
});
