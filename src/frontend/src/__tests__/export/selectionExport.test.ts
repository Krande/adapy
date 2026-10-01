// `@/utils/export/selectionExport` -- which tree row a selection stands for, whether its model can
// be exported, the request the route is sent, and the request/poll/download flow. Pure, so it runs
// against hand-built trees and fake APIs: no stores, no network, no timers.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import {
  SelectionExportError,
  describeExportError,
  elementPath,
  modelRootOf,
  parseAssetSourceName,
  planSelectionExport,
  resolveSelectedRow,
  runSelectionExport,
  selectionExportBody,
  type SelectionExportDeps,
} from "@/utils/export/selectionExport";
import { buildTreeIndices } from "@/utils/tree_view/treeGraph";

function n(id: string, name: string, children: TreeNodeData[] = [], extra: Partial<TreeNodeData> = {}): TreeNodeData {
  return { id, name, children, ...extra };
}

// Two models side by side under the synthetic container, as `cacheAndBuildTree` holds them. The
// file model has the SAME leaf name ("PL1") in two decks -- the case the path exists for.
function scene(fileSource = "models/plant.ifc") {
  const plA = n("4", "PL1", [], { model_key: "m1", rangeId: "3", node_name: "node0" });
  const bm = n("5", "BM1", [], { model_key: "m1", rangeId: "4", node_name: "node0" });
  const deckA = n("3", "deck_a", [plA, bm], { model_key: "m1", rangeId: "2" });
  const plB = n("7", "PL1", [], { model_key: "m1", rangeId: "6", node_name: "node0" });
  const deckB = n("6", "deck_b", [plB], { model_key: "m1", rangeId: "5" });
  const fileRoot = n("2", "Plant (relabelled)", [deckA, deckB], {
    model_key: "m1",
    rangeId: "1",
    source_name: fileSource,
    top_name: "Plant",
  });
  const leaf = n("10", "member-3", [], { model_key: "m2", rangeId: "1", node_name: "node0" });
  const assetRoot = n("9", "Area 1", [leaf], {
    model_key: "m2",
    rangeId: "0",
    source_name: "assets:fixture-lines/plant-a/area-1@20260901T100000Z",
    top_name: "Area 1",
  });
  const container = n("__roots__", "", [fileRoot, assetRoot]);
  return { idx: buildTreeIndices(container), fileRoot, deckA, plA, plB, assetRoot, leaf };
}

const byRange = (idx: ReturnType<typeof buildTreeIndices>) => (modelKey: string, rangeId: string) => {
  for (const node of idx.byId.values()) if (node.model_key === modelKey && node.rangeId === rangeId) return node;
  return null;
};

// --- parseAssetSourceName ------------------------------------------------------------

test("an asset source name parses back to its node", () => {
  assert.deepEqual(parseAssetSourceName("assets:fixture-lines/plant-a/area-1@20260901T100000Z"), {
    provider: "fixture-lines",
    collection: "plant-a",
    subject: "area-1",
    revision: "20260901T100000Z",
  });
  assert.deepEqual(parseAssetSourceName("assets:p/c/s@20260901T100000Z#member-3")?.node, "member-3");
});

test("a storage key is not an asset source name", () => {
  assert.equal(parseAssetSourceName("models/plant.ifc"), null);
});

// --- tree helpers ----------------------------------------------------------------------

test("the model root is the row directly under the container", () => {
  const { idx, plB, fileRoot, assetRoot } = scene();
  assert.equal(modelRootOf(plB, idx)?.id, fileRoot.id);
  assert.equal(modelRootOf(fileRoot, idx)?.id, fileRoot.id);
  assert.equal(modelRootOf(assetRoot, idx)?.id, assetRoot.id);
});

test("the path leaves the relabelled root row out and ends at the element", () => {
  const { idx, plB, fileRoot } = scene();
  assert.deepEqual(elementPath(plB, fileRoot, idx), ["deck_b", "PL1"]);
});

// --- resolveSelectedRow ------------------------------------------------------------------

test("a tree click on a level is that level while the selection is its geometry", () => {
  const { idx, deckA } = scene();
  const row = resolveSelectedRow({
    indices: idx,
    selectedNodeId: deckA.id,
    name: "deck_a",
    ranges: [["m1", "3"], ["m1", "4"]],
    findByRange: byRange(idx),
  });
  assert.equal(row?.id, deckA.id);
});

test("a stale tree selection is not trusted over a single pick of a same-named element", () => {
  // The tree last selected deck_a's PL1; then deck_b's PL1 was clicked in the 3D view, which does
  // not touch the tree selection. Name alone would export the wrong plate.
  const { idx, plA, plB } = scene();
  const row = resolveSelectedRow({
    indices: idx,
    selectedNodeId: plA.id,
    name: "PL1",
    ranges: [["m1", "6"]],
    findByRange: byRange(idx),
  });
  assert.equal(row?.id, plB.id);
});

test("several picks with no row holding them all are not one selection", () => {
  const { idx } = scene();
  const row = resolveSelectedRow({
    indices: idx,
    selectedNodeId: null,
    name: "PL1",
    ranges: [["m1", "3"], ["m1", "6"]],
    findByRange: byRange(idx),
  });
  assert.equal(row, null);
});

// --- planSelectionExport -----------------------------------------------------------------

test("no server: nothing to export with", () => {
  const { idx, plB } = scene();
  const plan = planSelectionExport({ restMode: false, row: plB, indices: idx, loadedAssets: [] });
  assert.equal(plan.ok, false);
});

test("an element of a file model is named with its path", () => {
  const { idx, plB } = scene();
  const plan = planSelectionExport({ restMode: true, row: plB, indices: idx, loadedAssets: [] });
  assert.ok(plan.ok);
  assert.deepEqual(plan.request, {
    target: { kind: "file", sourceKey: "models/plant.ifc" },
    element: "PL1",
    path: ["deck_b", "PL1"],
    label: "Plant",
  });
});

test("the root row is the whole model", () => {
  const { idx, fileRoot } = scene();
  const plan = planSelectionExport({ restMode: true, row: fileRoot, indices: idx, loadedAssets: [] });
  assert.ok(plan.ok);
  assert.equal(plan.request.element, null);
  assert.deepEqual(plan.request.path, []);
});

test("a GLB has no source behind it", () => {
  const { idx, plB } = scene("models/mesh.glb");
  const plan = planSelectionExport({ restMode: true, row: plB, indices: idx, loadedAssets: [] });
  assert.equal(plan.ok, false);
  assert.match(plan.ok ? "" : plan.reason, /GLB/);
});

test("a source whose tree is not objects is refused by extension", () => {
  const { idx, plB } = scene("models/deck.fem");
  const plan = planSelectionExport({ restMode: true, row: plB, indices: idx, loadedAssets: [] });
  assert.equal(plan.ok, false);
  assert.match(plan.ok ? "" : plan.reason, /\.fem source/);
});

test("a built provider node is exported through its provider", () => {
  const { idx, leaf, assetRoot } = scene();
  const ref = { provider: "fixture-lines", collection: "plant-a", subject: "area-1", revision: "20260901T100000Z" };
  const plan = planSelectionExport({
    restMode: true,
    row: leaf,
    indices: idx,
    loadedAssets: [{ sourceName: assetRoot.source_name as string, ref, glbKey: "_derived/x/model.glb" }],
  });
  assert.ok(plan.ok);
  assert.deepEqual(plan.request.target, { kind: "node", ...ref, node: "area-1" });
  assert.equal(plan.request.element, "member-3");
});

test("a provider node delivered as a mesh is refused, naming the provider", () => {
  const { idx, leaf, assetRoot } = scene();
  const ref = { provider: "fixture-lines", collection: "plant-a", subject: "area-1", revision: "20260901T100000Z" };
  const plan = planSelectionExport({
    restMode: true,
    row: leaf,
    indices: idx,
    loadedAssets: [{ sourceName: assetRoot.source_name as string, ref }],
  });
  assert.equal(plan.ok, false);
  assert.match(plan.ok ? "" : plan.reason, /fixture-lines delivered this model as a mesh/);
});

test("the body names the model exactly one way", () => {
  const body = selectionExportBody(
    { target: { kind: "file", sourceKey: "models/plant.ifc" }, element: "PL1", path: ["deck_b", "PL1"], label: "Plant" },
    "step",
  );
  assert.deepEqual(body, {
    source_key: "models/plant.ifc",
    format: "step",
    element: "PL1",
    path: ["deck_b", "PL1"],
    label: "Plant",
  });
  assert.equal("collection" in body, false);
});

// --- runSelectionExport ------------------------------------------------------------------

const REQUEST = {
  target: { kind: "file", sourceKey: "models/plant.ifc" },
  element: "deck_a",
  path: ["deck_a"],
  label: "Plant",
} as const;

function fakeDeps(statuses: { status: string; error: string | null }[], cached = false) {
  const calls = { status: 0, downloads: [] as string[], tracked: [] as string[] };
  const deps: SelectionExportDeps = {
    api: {
      exportSelection: async () => ({
        job_id: cached ? null : "job-1",
        derived_key: "_derived/export/t/s/deck_a.step",
        cached,
        filename: "deck_a.step",
      }),
      jobStatus: async () => statuses[Math.min(calls.status++, statuses.length - 1)],
    },
    download: async (_scope, key, filename) => void calls.downloads.push(`${key} as ${filename}`),
    trackJob: (o) => void calls.tracked.push(o.label),
    wait: async () => {},
  };
  return { deps, calls };
}

test("a job is polled to done, toasted, then downloaded under the route's name", async () => {
  const { deps, calls } = fakeDeps([
    { status: "running", error: null },
    { status: "done", error: null },
  ]);
  const out = await runSelectionExport(deps, "user:me", REQUEST, "step");
  assert.equal(out.cached, false);
  assert.equal(calls.status, 2);
  assert.deepEqual(calls.tracked, ["Export: deck_a.step"]);
  assert.deepEqual(calls.downloads, ["_derived/export/t/s/deck_a.step as deck_a.step"]);
});

test("a stored export downloads without polling", async () => {
  const { deps, calls } = fakeDeps([], true);
  await runSelectionExport(deps, "user:me", REQUEST, "ifc");
  assert.equal(calls.status, 0);
  assert.equal(calls.downloads.length, 1);
});

test("a failed job is refused with the job's own reason and nothing is downloaded", async () => {
  const { deps, calls } = fakeDeps([{ status: "error", error: "no element named 'deck_a'" }]);
  await assert.rejects(runSelectionExport(deps, "user:me", REQUEST, "step"), (e: unknown) => {
    assert.ok(e instanceof SelectionExportError);
    assert.match((e as Error).message, /no element named/);
    return true;
  });
  assert.equal(calls.downloads.length, 0);
});

test("a refused request shows the route's detail, not the status line", () => {
  const err = Object.assign(new Error("exportSelection failed: 409 Conflict"), {
    detail: JSON.stringify({ detail: "subject 'x' claims delivery 'mesh'" }),
  });
  assert.equal(describeExportError(err), "subject 'x' claims delivery 'mesh'");
  assert.equal(describeExportError(new Error("boom")), "boom");
});
