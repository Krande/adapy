// The Assets tab's own store: this file covers only the Phase-3 load-tracking
// slice added alongside `./delivery` (busy/error per row, the `loaded` mirror,
// and reconciliation against the scene's live source-name set). The rest of
// the store is exercised through `assetBrowserLoader.test.ts`.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

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
