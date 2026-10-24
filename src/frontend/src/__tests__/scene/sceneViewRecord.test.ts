// The hidden objects and the selection kept with the scene record: their stored shape, size
// bound and validation, older records without them, and re-applying them after a reload.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import * as THREE from "three";

import {loadedSourceGroups} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore, type ViewerHistoryEntry} from "@/state/viewerHistoryStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {
    compactVisibility,
    fitView,
    missingNote,
    parseSceneRecord,
    planSourceView,
    serialiseSceneRecord,
    type RecordedView,
    type SceneRecord,
} from "@/utils/scene/sceneRecord";
import {applyRecordedView, captureView} from "@/utils/scene/sceneViewRestore";

const model: SceneRecord["models"][number] = {kind: "file", name: "models/a.glb"};

test("a record written before views were kept still loads, with no view", () => {
    const old = JSON.stringify({v: 1, models: [model], camera: null});
    const r = parseSceneRecord(old);
    assert.deepEqual(r.models, [model]);
    assert.equal(r.view, undefined);
    assert.equal(serialiseSceneRecord(r), old, "and is written back unchanged");
});

test("the view round-trips through the record", () => {
    const view: RecordedView = {v: 1, sources: {"models/a.glb": {hidden: ["e1", "e2"], selected: ["e3"]}, "models/b.glb": {shown: ["x"]}}};
    const r = parseSceneRecord(serialiseSceneRecord({v: 1, models: [model], camera: null, view}));
    assert.deepEqual(r.view, view);
});

test("a malformed view is dropped, not the record", () => {
    for (const bad of [{v: 2, sources: {}}, {v: 1, sources: []}, {v: 1, sources: {a: "x"}}, "nope"]) {
        const r = parseSceneRecord(JSON.stringify({v: 1, models: [model], camera: null, view: bad}));
        assert.deepEqual(r.models, [model]);
        assert.equal(r.view, undefined);
    }
    const mixed = parseSceneRecord(JSON.stringify({v: 1, models: [model], camera: null, view: {v: 1, sources: {a: {hidden: ["ok", 3, ""], shown: ["k"]}}}}));
    assert.deepEqual(mixed.view, {v: 1, sources: {a: {shown: ["k"]}}}, "an isolation wins over a hidden list; junk ids go");
});

test("visibility is stored the shorter way round", () => {
    assert.equal(compactVisibility([], ["a", "b"]), null);
    assert.deepEqual(compactVisibility(["b", "a"], ["c", "d", "e"]), {hidden: ["a", "b"]});
    assert.deepEqual(compactVisibility(["a", "b", "c"], ["d"]), {shown: ["d"]}, "an isolation keeps what is drawn");
});

test("a view too large for the record drops the selection first, then itself", () => {
    const ids = (n: number, p: string) => Array.from({length: n}, (_, i) => `${p}${i}`);
    const view: RecordedView = {v: 1, sources: {a: {hidden: ids(10, "h"), selected: ids(1000, "s")}}};
    const size = (v: RecordedView | null) => (v ? JSON.stringify(v).length : 0);
    assert.equal(fitView(view, size(view)), view);
    assert.deepEqual(fitView(view, size(view) - 1), {v: 1, sources: {a: {hidden: ids(10, "h")}}});
    assert.equal(fitView(view, 20), null);
    // Thousands of hidden ids fit comfortably within the default bound.
    assert.ok(fitView({v: 1, sources: {a: {hidden: ids(20000, "element-")}}}));
});

test("re-applying a model's view: per mesh, and counting what is gone", () => {
    const plan = planSourceView({hidden: ["a", "c", "gone"], selected: ["b", "lost"]}, [new Set(["a", "b"]), new Set(["c"])]);
    assert.deepEqual(plan.perMesh.map((p) => p.hide), [["a"], ["c"]]);
    assert.deepEqual(plan.perMesh.map((p) => p.select), [["b"], []]);
    assert.deepEqual(plan.missing, {hidden: 1, selected: 1});
    const iso = planSourceView({shown: ["b"]}, [new Set(["a", "b"])]);
    assert.deepEqual([...iso.perMesh[0].keep!], ["b"]);
    assert.equal(missingNote({hidden: 3, selected: 0}), "3 hidden objects were not found");
    assert.equal(missingNote({hidden: 1, selected: 0}), "1 hidden object was not found");
    assert.equal(missingNote({hidden: 0, selected: 0}), null);
});

// --- against a headless scene ---------------------------------------------------------------

function mesh(ids: string[]): CustomBatchedMesh {
    const pos: number[] = [];
    ids.forEach((_, i) => pos.push(i, 0, 0, i + 1, 0, 0, i + 1, 1, 0));
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
    return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), new Map(ids.map((id, i) => [id, [i * 3, 3]])), "m", true, null);
}

/** One model of `ids` loaded as `name`, as a load registers it. */
function load(name: string, ids: string[]): CustomBatchedMesh {
    const group = new THREE.Group();
    const m = mesh(ids);
    group.add(m);
    new THREE.Scene().add(group);
    loadedSourceGroups.set(name, group);
    return m;
}

beforeEach(() => {
    loadedSourceGroups.clear();
    useSelectedObjectStore.getState().clearSelectedObjects();
    useViewerHistoryStore.getState().reset();
});

test("hidden objects and the selection survive a reload, by element id", async () => {
    const before = load("models/a.glb", ["e0", "e1", "e2", "e3"]);
    before.hideBatchDrawRange(["e1"]);
    useSelectedObjectStore.getState().addBatchofMeshes([[before, "e2"], [before, "e3"]]);
    const view = captureView();
    assert.deepEqual(view, {v: 1, sources: {"models/a.glb": {hidden: ["e1"], selected: ["e2", "e3"]}}});

    // The reload: new scene objects, the same element ids, and an undo history to be cleared.
    loadedSourceGroups.clear();
    useSelectedObjectStore.getState().clearSelectedObjects();
    const after = load("models/a.glb", ["e0", "e1", "e2", "e3"]);
    const noop: ViewerHistoryEntry = {label: "x", undo: () => {}, redo: () => {}};
    useViewerHistoryStore.getState().record(noop);

    const missing = await applyRecordedView(view, []);
    assert.deepEqual(missing, {hidden: 0, selected: 0});
    assert.deepEqual([...after.getHiddenRanges()], ["e1"]);
    assert.deepEqual([...(useSelectedObjectStore.getState().selectedObjects.get(after) ?? [])].sort(), ["e2", "e3"]);
    assert.equal(useViewerHistoryStore.getState().past.length, 0, "restoring is not an undo step");
});

test("an isolation is restored as one, and missing ids are only counted", async () => {
    const m = load("models/a.glb", ["e0", "e1", "e2"]);
    const missing = await applyRecordedView({v: 1, sources: {"models/a.glb": {shown: ["e0", "old"], selected: ["e2", "gone"]}, "models/unloaded.glb": {hidden: ["z"]}}}, []);
    assert.deepEqual([...m.getHiddenRanges()].sort(), ["e1", "e2"]);
    assert.equal(useSelectedObjectStore.getState().selectedObjects.size, 0, "e2 is hidden now, so not selected");
    assert.deepEqual(missing, {hidden: 1, selected: 1}, "a model not loaded is skipped quietly");
});
