// Models loaded and removed, selection boxes and section planes as undo / redo steps, against a
// headless scene. Loading again is faked here (the real one is the page-restore path): it builds
// a new group with the same element ids, as a reload of the same file would.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import * as THREE from "three";

import {useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {loadedSourceGroups, useModelState} from "@/state/modelState";
import {useSectionStore} from "@/state/sectionStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {createViewerRuntime, mountViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {bindModelHistory, type ModelHistoryDeps} from "@/utils/scene/modelHistory";
import type {RecordedModel} from "@/utils/scene/sceneRecord";
import {recordViewerAction, redoViewerAction, undoViewerAction} from "@/utils/scene/viewerHistory";
import {hideSelectedRanges} from "@/utils/scene/visibility";

const IDS = ["e0", "e1", "e2", "e3"];
const runtime = createViewerRuntime();
mountViewerRuntime(runtime);
let scene: THREE.Scene;

function mesh(ids: string[]): CustomBatchedMesh {
    const pos: number[] = [];
    ids.forEach((_, i) => pos.push(i, 0, 0, i + 1, 0, 0, i + 1, 1, 0));
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
    return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), new Map(ids.map((id, i) => [id, [i * 3, 3]])), "m", true, null);
}

/** Put a model in the scene the way a load does. */
function addModel(name: string): void {
    const group = new THREE.Group();
    group.add(mesh(IDS));
    scene.add(group);
    useModelState.getState().registerLoadedSource(name, group);
}

const meshOf = (name: string): CustomBatchedMesh => loadedSourceGroups.get(name)!.children[0] as CustomBatchedMesh;
const hist = () => useViewerHistoryStore.getState();
const settle = async () => {
    while (hist().pending) await hist().pending;
};

let reloadable = true;
const loads: string[] = [];
const deps: ModelHistoryDeps = {
    describe: (name) => (reloadable ? ({kind: "file", name} as RecordedModel) : null),
    load: async (m) => {
        await new Promise((r) => setTimeout(r, 5));
        const name = (m as {name: string}).name;
        loads.push(name);
        addModel(name);
        return true;
    },
    unload: (name) => {
        const group = useModelState.getState().unregisterLoadedSource(name);
        group?.removeFromParent();
    },
    shouldRecord: () => true,
};
bindModelHistory(deps);

beforeEach(() => {
    for (const name of [...useModelState.getState().loadedSourceNames]) deps.unload(name);
    scene = new THREE.Scene();
    (runtime.scene as {current: THREE.Scene | null}).current = scene;
    useSelectedObjectStore.getState().clearSelectedObjects();
    useBBoxSelectionStore.setState({boxes: [], activeId: null, menu: null});
    useSectionStore.setState({planes: [], activeId: null});
    reloadable = true;
    loads.length = 0;
    hist().reset();
});

test("loading a model is one step: undo removes it, redo loads it again", async () => {
    addModel("models/a.glb");
    assert.deepEqual(hist().past.map((e) => e.label), ["load a.glb"]);
    undoViewerAction();
    await settle();
    assert.ok(!useModelState.getState().loadedSourceNames.has("models/a.glb"));
    redoViewerAction();
    await settle();
    assert.ok(useModelState.getState().loadedSourceNames.has("models/a.glb"));
    assert.deepEqual(loads, ["models/a.glb"], "loaded again through the reload path");
    assert.equal(hist().past.length, 1, "the reload records nothing of its own");
});

test("undoing a removal loads the model again with what was hidden and selected in it", async () => {
    addModel("models/a.glb");
    const first = meshOf("models/a.glb");
    recordViewerAction("select", () => useSelectedObjectStore.getState().addBatchofMeshes([[first, "e0"]]));
    hideSelectedRanges(); // e0 hidden
    recordViewerAction("select", () => useSelectedObjectStore.getState().addBatchofMeshes([[first, "e2"]]));
    deps.unload("models/a.glb");
    assert.equal(hist().past[hist().past.length - 1].label, "remove a.glb");

    undoViewerAction();
    await settle();
    const again = meshOf("models/a.glb");
    assert.notEqual(again, first, "a new mesh, loaded again");
    assert.deepEqual([...again.getHiddenRanges()], ["e0"]);
    assert.deepEqual([...(useSelectedObjectStore.getState().selectedObjects.get(again) ?? [])], ["e2"]);

    // The steps before the removal name the model, not its old mesh: they work on the new one.
    undoViewerAction(); // the selection of e2
    undoViewerAction(); // the hide of e0
    assert.deepEqual([...again.getHiddenRanges()], []);
    assert.deepEqual([...(useSelectedObjectStore.getState().selectedObjects.get(again) ?? [])], ["e0"]);
});

test("no other undo or redo starts while a model is loading again", async () => {
    addModel("models/a.glb");
    recordViewerAction("select", () => useSelectedObjectStore.getState().addBatchofMeshes([[meshOf("models/a.glb"), "e1"]]));
    deps.unload("models/a.glb");
    assert.ok(undoViewerAction(), "the removal's undo starts");
    assert.ok(hist().applying);
    assert.equal(undoViewerAction(), null, "a second undo is ignored while loading");
    assert.equal(redoViewerAction(), null, "and so is a redo");
    await settle();
    assert.ok(!hist().applying);
    assert.equal(undoViewerAction(), "select 1 object", "the next undo runs once it settled");
});

test("a model that cannot be loaded again leaves no removal step", () => {
    addModel("dropped.glb");
    reloadable = false;
    deps.unload("dropped.glb");
    assert.deepEqual(hist().past.map((e) => e.label), ["load dropped.glb"]);
});

test("selection boxes: adding, editing and deleting are steps; a drag's moves are one", () => {
    const st = () => useBBoxSelectionStore.getState();
    const id = st().add({min: [0, 0, 0], max: [1, 1, 1]});
    for (let i = 1; i <= 5; i++) st().update(id, {max: [1 + i, 1, 1]});
    st().remove(id);
    assert.deepEqual(hist().past.map((e) => e.label), ["add selection box", "edit selection box", "delete selection box"]);

    undoViewerAction();
    assert.deepEqual(st().boxes.map((b) => b.max[0]), [6], "deleting undone");
    undoViewerAction();
    assert.deepEqual(st().boxes.map((b) => b.max[0]), [1], "the whole drag undone");
    undoViewerAction();
    assert.equal(st().boxes.length, 0, "adding undone");
    redoViewerAction();
    redoViewerAction();
    redoViewerAction();
    assert.equal(st().boxes.length, 0);
    assert.equal(hist().past.length, 3);
});

test("section planes: add, move and remove are steps", () => {
    const st = () => useSectionStore.getState();
    st().addPlane("z");
    const id = st().planes[0].id;
    st().setConstant(id, -1);
    st().setConstant(id, -2);
    st().removePlane(id);
    assert.deepEqual(hist().past.map((e) => e.label), ["add section plane", "edit section plane", "remove section plane"]);
    undoViewerAction();
    assert.equal(st().planes[0].constant, -2);
    undoViewerAction();
    assert.equal(st().planes[0].constant, -0);
    undoViewerAction();
    assert.equal(st().planes.length, 0);
});
