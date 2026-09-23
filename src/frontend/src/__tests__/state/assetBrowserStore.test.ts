// The Assets tab's own store: the Phase-3 load-tracking slice added alongside
// `./delivery` (busy/error per row, the `loaded` mirror, and reconciliation
// against the scene's live source-name set), and the Phase-4 change-feed
// slice (`sourceAnswer`/`changedRows`/`evidenceAsked`, folded by
// `mergeSourceAnswer`). The rest of the store -- and the LAZY FETCHING that
// drives `mergeSourceAnswer` -- is exercised through `assetBrowserLoader.test.ts`;
// this file pins the fold itself, called directly, network-free.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type { SourceNodeRow, SourceNodesAnswer } from "../../assets/changes";
import type { LoadedAsset } from "../../assets/delivery";
import { useAssetBrowserStore } from "../../state/assetBrowserStore";

const asset = (sourceName: string): LoadedAsset => ({
  sourceName,
  ref: { provider: "fixture-lines", collection: "plant-a", subject: "area-1", revision: "20260901T100000Z" },
  revision: "20260901T100000Z",
  provider: "fixture-lines",
});

beforeEach(() => {
  useAssetBrowserStore.setState({
    loaded: [],
    loadBusy: new Set(),
    loadErrors: new Map(),
  });
});

test("beginLoad marks a row busy and clears its previous error", () => {
  const s = useAssetBrowserStore.getState();
  s.failLoad("row-1", "boom");
  assert.equal(useAssetBrowserStore.getState().loadErrors.get("row-1"), "boom");
  s.beginLoad("row-1");
  const after = useAssetBrowserStore.getState();
  assert.ok(after.loadBusy.has("row-1"));
  assert.equal(after.loadErrors.has("row-1"), false);
});

test("endLoad clears busy and appends the asset once", () => {
  const s = useAssetBrowserStore.getState();
  s.beginLoad("row-1");
  s.endLoad("row-1", asset("assets:fixture-lines/plant-a/area-1@20260901T100000Z"));
  const after = useAssetBrowserStore.getState();
  assert.equal(after.loadBusy.has("row-1"), false);
  assert.equal(after.loaded.length, 1);
});

test("endLoad from a second row covered by the same ancestor does not duplicate the entry", () => {
  const s = useAssetBrowserStore.getState();
  const a = asset("assets:fixture-lines/plant-a/area-1@20260901T100000Z#member-3");
  s.endLoad("row-a", a);
  s.endLoad("row-b", { ...a }); // same sourceName, a different row's load finished
  assert.equal(useAssetBrowserStore.getState().loaded.length, 1);
});

test("failLoad clears busy and records the message", () => {
  const s = useAssetBrowserStore.getState();
  s.beginLoad("row-1");
  s.failLoad("row-1", "network down");
  const after = useAssetBrowserStore.getState();
  assert.equal(after.loadBusy.has("row-1"), false);
  assert.equal(after.loadErrors.get("row-1"), "network down");
});

test("reconcileLoaded drops an entry the scene no longer holds", () => {
  const s = useAssetBrowserStore.getState();
  const kept = asset("assets:fixture-lines/plant-a/area-1@20260901T100000Z");
  const dropped = asset("assets:fixture-lines/plant-a/area-2@20260901T100000Z");
  s.endLoad("row-1", kept);
  s.endLoad("row-2", dropped);
  s.reconcileLoaded(new Set([kept.sourceName])); // as if area-2 was unloaded elsewhere
  const names = useAssetBrowserStore.getState().loaded.map((a) => a.sourceName);
  assert.deepEqual(names, [kept.sourceName]);
});

test("reconcileLoaded is a no-op (same reference) when nothing changed", () => {
  const s = useAssetBrowserStore.getState();
  const kept = asset("assets:fixture-lines/plant-a/area-1@20260901T100000Z");
  s.endLoad("row-1", kept);
  const before = useAssetBrowserStore.getState().loaded;
  s.reconcileLoaded(new Set([kept.sourceName]));
  assert.equal(useAssetBrowserStore.getState().loaded, before);
});

// ---------------------------------------------------------------------------
// mergeSourceAnswer -- the change-feed slice
// ---------------------------------------------------------------------------

function row(nodeRef: string, action: SourceNodeRow["action"] = null): SourceNodeRow {
  return { nodeRef, parentRef: null, name: null, lastChangedAt: "2026-08-27T00:00:00Z", lastChangedBy: null, observedAt: "2026-08-27T00:00:00Z", action };
}

function answer(source: string, rows: readonly SourceNodeRow[]): SourceNodesAnswer {
  return { source, rows: new Map(rows.map((r) => [r.nodeRef, r])), unknown: new Set() };
}

beforeEach(() => {
  useAssetBrowserStore.setState({
    sourceAnswer: new Map(),
    changedRows: new Map(),
    evidenceAsked: new Set(),
  });
});

test("mergeSourceAnswer records every asked ref, whether or not the feed had a row for it", () => {
  const s = useAssetBrowserStore.getState();
  s.mergeSourceAnswer("provider-x", answer("provider-x", [row("site-a")]), ["site-a", "site-b"]);
  const after = useAssetBrowserStore.getState();
  assert.ok(after.evidenceAsked.has("site-a"));
  assert.ok(after.evidenceAsked.has("site-b"), "asked and got nothing back is still having asked");
});

test("mergeSourceAnswer flattens rows with a non-null action into changedRows, across providers", () => {
  const s = useAssetBrowserStore.getState();
  s.mergeSourceAnswer("provider-x", answer("provider-x", [row("member-3", "modified"), row("site-a", null)]), ["member-3", "site-a"]);
  s.mergeSourceAnswer("provider-y", answer("provider-y", [row("member-9", "added")]), ["member-9"]);
  const after = useAssetBrowserStore.getState();
  assert.equal(after.changedRows.get("member-3")?.action, "modified");
  assert.equal(after.changedRows.has("site-a"), false, "action is null -- a roll-up row, not evidence");
  assert.equal(after.changedRows.get("member-9")?.action, "added");
});

test("mergeSourceAnswer with a null answer (no-feed) replaces that provider's rows outright, not merges", () => {
  const s = useAssetBrowserStore.getState();
  s.mergeSourceAnswer("provider-x", answer("provider-x", [row("member-3", "modified")]), ["member-3"]);
  assert.equal(useAssetBrowserStore.getState().changedRows.has("member-3"), true);
  s.mergeSourceAnswer("provider-x", null, ["member-3"]);
  const after = useAssetBrowserStore.getState();
  assert.equal(after.sourceAnswer.get("provider-x"), null);
  assert.equal(after.changedRows.has("member-3"), false, "a provider that just said no-feed cannot still justify a stale evidence row");
  assert.ok(after.evidenceAsked.has("member-3"), "still asked -- the feed answered no-feed, it did not go unasked");
});

test("mergeSourceAnswer for one provider does not disturb another provider's rows", () => {
  const s = useAssetBrowserStore.getState();
  s.mergeSourceAnswer("provider-x", answer("provider-x", [row("member-3", "modified")]), ["member-3"]);
  s.mergeSourceAnswer("provider-y", null, ["member-9"]);
  const after = useAssetBrowserStore.getState();
  assert.equal(after.changedRows.get("member-3")?.action, "modified", "provider-y going no-feed must not wipe provider-x's rows");
  assert.equal(after.sourceAnswer.get("provider-y"), null);
});
