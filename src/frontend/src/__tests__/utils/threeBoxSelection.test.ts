// Box selection on the three.js viewport, behind the viewport interface: what a box and a screen
// rectangle take is decided on the triangles as before, handed over grouped by mesh, and selected as
// one undo step.
import {beforeEach, test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {loadedSourceGroups} from "@/state/modelState";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {addBoxAroundSelection, applyBoxSelection, selectInScreenRect} from "@/utils/bboxSelection/applyBoxSelection";
import type {Aabb} from "@/utils/bboxSelection/boxMath";
import {RangeSet, rangesInWorldBox} from "@/utils/bboxSelection/threeBoxSelection";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {undoViewerAction} from "@/utils/scene/viewerHistory";
import {createThreeViewport} from "@/viewport/api/threeViewport";
import {activeViewport, setActiveViewport} from "@/viewport/api/viewportApi";

const unit: Aabb = {min: [0, 0, 0], max: [1, 1, 1]};

/** A mesh of three elements: A inside the unit box, B straddling its +X face, C far away. */
function threeElements(): CustomBatchedMesh {
    const tris = [
        [[0.2, 0.2, 0.5], [0.8, 0.2, 0.5], [0.5, 0.8, 0.5]],
        [[0.5, 0.5, 0.5], [3, 0.5, 0.5], [3, 0.9, 0.5]],
        [[10, 10, 10], [11, 10, 10], [10, 11, 10]],
    ];
    const pos: number[] = [];
    for (const t of tris) for (const v of t) pos.push(...v);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
    const ranges = new Map<string, [number, number]>([["A", [0, 3]], ["B", [3, 3]], ["C", [6, 3]]]);
    const mesh = new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "m", true, null);
    mesh.name = "node";
    return mesh;
}

const runtime = getViewerRuntime();
const selected = (mesh: THREE.Object3D) => [...(useSelectedObjectStore.getState().selectedObjects.get(mesh) ?? [])].sort();
const steps = () => useViewerHistoryStore.getState().past.map((e) => e.label);
let mesh: CustomBatchedMesh;
let withdraw: (() => void) | null = null;

beforeEach(() => {
    withdraw?.();
    const scene = new THREE.Scene();
    mesh = threeElements();
    scene.add(mesh);
    (runtime.scene as {current: THREE.Scene | null}).current = scene;
    // A camera on +Z looking at the origin, as the screen rectangle sees the scene.
    const camera = new THREE.PerspectiveCamera(90, 1, 0.1, 100);
    camera.position.set(0, 0, 5);
    camera.lookAt(0, 0, 0);
    withdraw = setActiveViewport(createThreeViewport({scene, camera, controls: {} as never}));
    useSelectedObjectStore.getState().clearSelectedObjects();
    useBBoxSelectionStore.setState({boxes: [], activeId: null, menu: null});
    useObjectInfoStore.getState().setName(null);
    useViewerHistoryStore.getState().reset();
});

test("what a box takes comes grouped by mesh, and reads as object refs of the model and its node", () => {
    const taken = rangesInWorldBox(unit, "partial");
    assert.ok(taken instanceof RangeSet);
    assert.equal(taken.size, 2);
    assert.deepEqual(taken.groups.map(([m, ids]) => [m, [...ids].sort()]), [[mesh, ["A", "B"]]]);
    const refs: string[] = [];
    const seen = new Set<unknown>();
    for (const ref of taken) {
        refs.push(`${ref.model}/${ref.node}/${ref.id}`);
        seen.add(ref);
    }
    assert.deepEqual(refs.sort(), ["m/node/A", "m/node/B"]);
    assert.equal(seen.size, 1, "one ref, reused: no object per range");
    assert.deepEqual(taken.pairs().map(([m, id]) => [m === mesh, id]).sort(), [[true, "A"], [true, "B"]]);
    assert.equal(rangesInWorldBox(unit, "full").size, 1);
});

test("applying a box replaces the selection in one undo step and names it; hidden ranges stay out", () => {
    const id = useBBoxSelectionStore.getState().add(unit);
    useSelectedObjectStore.getState().addBatchofMeshes([[mesh, "C"]]);
    useViewerHistoryStore.getState().reset();

    assert.equal(applyBoxSelection(id), 2);
    assert.deepEqual(selected(mesh), ["A", "B"]);
    assert.match(useObjectInfoStore.getState().name ?? "", /^Selection box \d+: 2 elements$/);
    assert.deepEqual(steps(), ["select 2 objects"]);
    // Applied again while a handle is dragged: still one step.
    applyBoxSelection(id);
    assert.equal(steps().length, 1);
    undoViewerAction();
    assert.deepEqual(selected(mesh), ["C"]);

    mesh.hideBatchDrawRange(new Set(["A"]));
    assert.equal(applyBoxSelection(id), 1);
    assert.deepEqual(selected(mesh), ["B"]);
});

test("a box that takes nothing clears the selection, as a selection step", () => {
    const id = useBBoxSelectionStore.getState().add({min: [50, 50, 50], max: [51, 51, 51]});
    useSelectedObjectStore.getState().addBatchofMeshes([[mesh, "A"]]);
    useViewerHistoryStore.getState().reset();
    assert.equal(applyBoxSelection(id), 0);
    assert.deepEqual(selected(mesh), []);
    assert.equal(steps().length, 1);
    assert.match(useObjectInfoStore.getState().name ?? "", /: 0 elements$/);
});

test("a box around the selection and what it then selects are one undo step", () => {
    useSelectedObjectStore.getState().addBatchofMeshes([[mesh, "A"]]);
    useViewerHistoryStore.getState().reset();
    const id = addBoxAroundSelection();
    assert.ok(id);
    const box = useBBoxSelectionStore.getState().boxes[0];
    // Around A (0.2 .. 0.8 in x and y, flat in z), a little larger.
    assert.ok(box.min[0] < 0.2 && box.min[0] > 0.1 && box.max[1] > 0.8 && box.max[1] < 0.9 && box.min[2] < 0.5 && box.max[2] > 0.5);
    assert.deepEqual(selected(mesh), ["A", "B"], "the box around A reaches B's triangle");
    assert.deepEqual(steps(), ["add selection box"]);
    undoViewerAction();
    assert.equal(useBBoxSelectionStore.getState().boxes.length, 0);
    assert.deepEqual(selected(mesh), ["A"]);
});

test("a rectangle on the screen adds what it takes; an empty one records nothing", () => {
    useSelectedObjectStore.getState().addBatchofMeshes([[mesh, "C"]]);
    useViewerHistoryStore.getState().reset();
    // A is drawn around (0.1, 0.1) of the view; the window holds it, and misses B, which runs on to x = 3.
    assert.equal(selectInScreenRect({x0: 0, y0: 0, x1: 0.2, y1: 0.2}, "window"), 1);
    assert.deepEqual(selected(mesh), ["A", "C"]);
    assert.equal(useObjectInfoStore.getState().name, "Window selection: 1 element");
    assert.equal(steps().length, 1);
    assert.equal(selectInScreenRect({x0: -1, y0: -1, x1: -0.9, y1: -0.9}, "crossing"), 0);
    assert.equal(steps().length, 1);
    assert.equal(selectInScreenRect({x0: 0, y0: 0, x1: 0.2, y1: 0.2}, "crossing"), 2);
    assert.deepEqual(selected(mesh), ["A", "B", "C"]);
});

test("ranges grouped by mesh are selected as the same ranges given one by one", () => {
    const store = useSelectedObjectStore.getState();
    mesh.hideBatchDrawRange(new Set(["B"]));
    store.addBatchofMeshes([[mesh, "C"]]);
    store.addBatchofMeshes([[mesh, "A"], [mesh, "B"]]);
    const pairs = selected(mesh);
    store.clearSelectedObjects();
    store.addBatchofMeshes([[mesh, "C"]]);
    const before = useSelectedObjectStore.getState().selectedObjects;
    store.addRangesByMesh([[mesh, ["A", "B"]]]);
    assert.deepEqual(selected(mesh), pairs);
    assert.deepEqual(pairs, ["A", "C"], "the hidden range is left out either way");
    assert.notEqual(useSelectedObjectStore.getState().selectedObjects, before, "a new map: subscribers see the change");
    // Nothing to add: the selection is the same object, so nothing re-renders.
    const same = useSelectedObjectStore.getState().selectedObjects;
    store.addRangesByMesh([[mesh, ["B"]]]);
    assert.equal(useSelectedObjectStore.getState().selectedObjects, same);
});

test("where a model's own origin is in the world: its source-space root, wherever the loader put it", () => {
    const viewport = activeViewport()!;
    assert.equal(viewport.sourceOrigin("proj/a.glb"), null, "no such model on screen");
    const group = new THREE.Group();
    group.position.set(1, 0, 0);
    const root = new THREE.Group();
    root.userData.__sourceSpaceRoot = true;
    root.position.set(10, 20, 30);
    group.add(root);
    loadedSourceGroups.set("proj/a.glb", group);
    try {
        assert.deepEqual(viewport.sourceOrigin("proj/a.glb"), [11, 20, 30]);
    } finally {
        loadedSourceGroups.delete("proj/a.glb");
    }
});
