// The selected objects' boxes in the model's own coordinates on the three.js viewport
// (`ViewportApi.selectionBoxes`, `ctx.scene.selectionBoxes`): the loader recentres what it loads,
// and a box comes back where the file has it, named as the Scene tree names the object.
import {afterEach, beforeEach, test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as THREE from "three";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {selectionBoxes} from "@/plugins/sceneOverlays";
import {useModelState} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {createThreeViewport} from "@/viewport/api/threeViewport";
import {setActiveViewport, type SelectedObjectBox} from "@/viewport/api/viewportApi";

/** The file's model far from the origin: plate A at x 1000..1002, plate B at x 1010..1011, a
 *  triangle each. */
function plates(): CustomBatchedMesh {
    const tris = [
        [[1000, 2000, 30], [1002, 2000, 30], [1000, 2001, 31]],
        [[1010, 2005, 30], [1011, 2005, 30], [1010, 2006, 30]],
    ];
    const pos: number[] = [];
    for (const t of tris) for (const v of t) pos.push(...v);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
    const ranges = new Map<string, [number, number]>([["A", [0, 3]], ["B", [3, 3]]]);
    const mesh = new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "m", true, null);
    mesh.name = "node0";
    return mesh;
}

const row = (id: string, name: string, children: TreeNodeData[] = [], rangeId?: string): TreeNodeData => ({
    id,
    name,
    children,
    model_key: "m",
    node_name: rangeId ? "node0" : null,
    rangeId: rangeId ?? null,
});

const OFFSET = new THREE.Vector3(-1005, -2003, -30);
let mesh: CustomBatchedMesh;
let withdraw: (() => void) | null = null;

beforeEach(() => {
    const scene = new THREE.Scene();
    mesh = plates();
    // What the loader does: the glTF scene is moved by the recentring offset, which it keeps.
    const gltfScene = new THREE.Group();
    gltfScene.userData.__sourceSpaceRoot = true;
    gltfScene.position.copy(OFFSET);
    gltfScene.add(mesh);
    scene.add(gltfScene);
    useModelState.getState().setTranslation(OFFSET.clone());
    (getViewerRuntime().scene as {current: THREE.Scene | null}).current = scene;
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    withdraw = setActiveViewport(createThreeViewport({scene, camera, controls: {} as never}));
    useSelectedObjectStore.getState().clearSelectedObjects();
    useTreeViewStore.getState().setTreeData({
        id: "__roots__",
        name: "",
        children: [row("1", "model.glb", [row("2", "Mini", [row("3", "deck_a", [row("4", "pl_a", [], "A"), row("5", "pl_b", [], "B")])])])],
    });
});

afterEach(() => {
    withdraw?.();
    withdraw = null;
    useTreeViewStore.getState().clearTreeData();
    useModelState.setState({translation: null});
});

const near = (got: number[], want: number[]) => got.forEach((v, k) => assert.ok(Math.abs(v - want[k]) < 1e-3, `${got} != ${want}`));

test("a selection gives a box per object, in the file's coordinates, named as the tree names it", () => {
    assert.deepEqual(selectionBoxes(), []);
    useSelectedObjectStore.getState().addRangesByMesh([[mesh, ["A", "B"]]]);
    const boxes = selectionBoxes().sort((a, b) => a.id.localeCompare(b.id));
    assert.deepEqual(
        boxes.map(({model, id, name, path}) => ({model, id, name, path})),
        [
            {model: "m", id: "A", name: "pl_a", path: ["model.glb", "Mini", "deck_a"]},
            {model: "m", id: "B", name: "pl_b", path: ["model.glb", "Mini", "deck_a"]},
        ],
    );
    near(boxes[0].min, [1000, 2000, 30]);
    near(boxes[0].max, [1002, 2001, 31]);
    near(boxes[1].min, [1010, 2005, 30]);
    near(boxes[1].max, [1011, 2006, 30]);
});

test("a hidden object that is selected is still there; an object the tree does not list has no names", () => {
    useSelectedObjectStore.getState().addRangesByMesh([[mesh, ["B"]]]);
    mesh.hideBatchDrawRange("B");
    assert.deepEqual(selectionBoxes().map((b) => b.id), ["B"]);
    useTreeViewStore.getState().clearTreeData();
    const [box] = selectionBoxes() as [SelectedObjectBox];
    assert.equal(box.name, null);
    assert.deepEqual(box.path, []);
});

test("without a 3D view a plugin is told nothing is selected", () => {
    useSelectedObjectStore.getState().addRangesByMesh([[mesh, ["A"]]]);
    withdraw?.();
    withdraw = null;
    assert.deepEqual(selectionBoxes(), []);
});

test("ctx.scene.selectionBoxes is the verb above", () => {
    // The plugin context imports the model worker, which cannot load outside a browser: its
    // scene handle is read from the source.
    const src = readFileSync(new URL("../../plugins/context.ts", import.meta.url), "utf8").replace(/\r\n/g, "\n");
    const handle = src.slice(src.indexOf("function makeSceneHandle"), src.indexOf("export function makePluginContext"));
    assert.match(handle, /^\s+selectionBoxes,$/m);
    assert.match(src, /^\s+selectionBoxes,\n[\s\S]*?\} from "\.\/sceneOverlays";/m);
});
