/**
 * The Assets tab's fetch side, driven against canned wire documents.
 *
 * Pins: the collection opened is the one with the newest publish; the tops of
 * the tree are the UNION of every collection index the mode admits, merged
 * oldest first so the newest word about a shared node wins even when an older
 * index arrives late; a spine is fetched once per (root, revision); and a
 * response for a collection the user has since left is dropped.
 */

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type { SourceNodesAnswer } from "../../assets/changes";
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
    [`plant-a|area-1|${R1}`]: slice("area-1", [
      ["area-1", null, "Area One", "area", 0, "build"],
      ["level-1", "area-1", "Level 1", "level", 0, ""],
      ["member-1", "level-1", "Member 1", "member", 1, "build"],
    ]),
  };
  const gates = new Map<string, () => void>();
  const api: AssetsApiLike = {
    async getAssetIndex(_scope, collection) {
      calls.push(`index:${collection ?? "*"}`);
      return indexFor[collection ?? "*"];
    },
    async getAssetTree(_scope, _provider, collection, opts) {
      const key = `${collection}|${opts.root ?? "index"}|${opts.revision}`;
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

test("a spine is fetched once per (root, revision) and merged under its root", async () => {
  const { api, calls } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  const source = { subject: "area-1", revision: R1, root: "area-1" };
  await loader.loadSpine(SCOPE, source);
  await loader.loadSpine(SCOPE, source);
  assert.equal(calls.filter((c) => c === `tree:plant-a|area-1|${R1}`).length, 1);
  const s = useAssetBrowserStore.getState();
  assert.equal(s.spineLoaded.get("area-1"), R1);
  assert.equal(s.forest.nodes.get("member-1")?.parent, "level-1");
  // The spine's top says parent=null; the forest keeps it a root either way here,
  // and its origin is now the spine's subject.
  assert.deepEqual(s.forest.origins.get("member-1"), { subject: "area-1", revision: R1 });
});

test("a failed spine is recorded against its root and retried explicitly", async () => {
  const { api } = makeApi();
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api);
  await loader.loadCollections(SCOPE);
  await loader.loadSpine(SCOPE, { subject: "area-2", revision: R2, root: "area-2" });
  const s = useAssetBrowserStore.getState();
  assert.match(s.spineErrors.get("area-2") ?? "", /no tree/);
  assert.ok(!s.spineLoading.has("area-2"));
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

test("per-spine evidence asks only the refs THIS spine brought in, deduped against what root evidence already asked", async () => {
  const { api } = makeApi();
  const { api: sourceApi, calls } = makeSourceNodesApi((source, refs) => ({
    source,
    rows: new Map(),
    unknown: new Set(refs),
  }));
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api, sourceApi);
  await loader.loadCollections(SCOPE); // asks about area-1 eagerly (root evidence)
  calls.length = 0;
  await loader.loadSpine(SCOPE, { subject: "area-1", revision: R1, root: "area-1" });
  assert.equal(calls.length, 1);
  // area-1 itself was already asked by root evidence -- the spine call's own
  // refs (its root plus every node id the slice brought in) are deduped
  // against the GLOBAL `evidenceAsked` set, not re-requested.
  assert.deepEqual([...calls[0].refs].sort(), ["level-1", "member-1"]);
  const s = useAssetBrowserStore.getState();
  assert.ok(s.evidenceAsked.has("level-1"));
  assert.ok(s.evidenceAsked.has("member-1"));
});

test("a second load of the same spine does not re-ask evidence for refs it already has", async () => {
  const { api } = makeApi();
  const { api: sourceApi, calls } = makeSourceNodesApi((source, refs) => ({ source, rows: new Map(), unknown: new Set(refs) }));
  const loader = createAssetBrowserLoader(useAssetBrowserStore, api, sourceApi);
  await loader.loadCollections(SCOPE);
  const source = { subject: "area-1", revision: R1, root: "area-1" };
  await loader.loadSpine(SCOPE, source);
  const before = calls.length;
  await loader.loadSpine(SCOPE, source); // idempotent: `spineLoaded` already matches
  assert.equal(calls.length, before, "the spine itself is not re-fetched, so evidence is not re-asked either");
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
  const source = { subject: "area-1", revision: R1, root: "area-1" };
  await loader.loadSpine(SCOPE, source);
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
  const key = `plant-a|area-1|${R1}`;
  gates.set(key, () => {});
  const pending = loader.loadSpine(SCOPE, { subject: "area-1", revision: R1, root: "area-1" });
  await new Promise((r) => setTimeout(r, 0));
  await loader.chooseCollection(SCOPE, "old-b");
  gates.get(key)!();
  await pending;
  const s = useAssetBrowserStore.getState();
  assert.equal(s.collection, "old-b");
  assert.ok(!s.forest.nodes.has("member-1"), "the late plant-a spine did not land in old-b's forest");
});
