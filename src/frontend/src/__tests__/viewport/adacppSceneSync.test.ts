// SceneSync mirrors the three.js scene into the adacpp WebGPU renderer. These tests drive it against a
// recording stand-in for the wasm module, so what reaches the renderer -- segments, per-range state,
// re-uploads -- is checked without a GPU.
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {SceneSync, SPRITE_LAYER} from "@/viewport/adacpp/sceneSync";
import {InstanceFlag, SegmentFlag, Topology, type AdacppModule, type WebRendererApi} from "@/viewport/adacpp/types";

interface Recorded {
    geometries: Map<number, {topology: number; vertexCount: number; segments: number[]}>;
    states: Map<number, number[]>;
    removed: number[];
    colorUpdates: number;
    morphUpdates: Map<number, number[]>; // geometry -> first morph component of each update
    influences: Map<number, number>; // geometry -> morph influence of its latest setInstance
    flags: Map<number, number>; // geometry -> flags of its latest setInstance
    clipPlanes: number[][] | null; // the latest setClipPlanes, as (nx, ny, nz, constant) rows
}

function fakeModule(): {module: AdacppModule; renderer: WebRendererApi; rec: Recorded} {
    const heap = new ArrayBuffer(1 << 20);
    let top = 16;
    const module = {
        HEAPU8: new Uint8Array(heap),
        HEAPU32: new Uint32Array(heap),
        HEAPF32: new Float32Array(heap),
        _malloc: (n: number) => {
            const p = top;
            top += (n + 15) & ~15;
            return p;
        },
        _free: () => {},
    } as unknown as AdacppModule;
    const u32 = (p: number, n: number) => (p ? Array.from(module.HEAPU32.subarray(p >> 2, (p >> 2) + n)) : []);
    const rec: Recorded = {geometries: new Map(), states: new Map(), removed: [], colorUpdates: 0, morphUpdates: new Map(), influences: new Map(), flags: new Map(), clipPlanes: null};
    const f32 = (p: number, n: number) => (p ? Array.from(module.HEAPF32.subarray(p >> 2, (p >> 2) + n)) : []);
    let nextGeom = 0;
    let nextInst = 0;
    const geomOfInst = new Map<number, number>();
    const renderer = {
        addGeometry: (topology: number, _p: number, vertexCount: number, _n: number, _c: number, _m: number,
                      _i: number, _ic: number, segments: number, segmentCount: number) => {
            const id = nextGeom++;
            rec.geometries.set(id, {topology, vertexCount, segments: u32(segments, segmentCount * 2)});
            return id;
        },
        addInstance: (geometry: number) => {
            const id = nextInst++;
            geomOfInst.set(id, geometry);
            return id;
        },
        setSegmentStates: (instance: number, packed: number, count: number) => {
            rec.states.set(instance, u32(packed, count * 2));
        },
        removeGeometry: (g: number) => rec.removed.push(g),
        updateColors: () => rec.colorUpdates++,
        setInstance: (instance: number, _m: number, _r: number, _g: number, _b: number, _a: number, flags: number,
                      influence: number) => {
            rec.influences.set(geomOfInst.get(instance)!, influence);
            rec.flags.set(geomOfInst.get(instance)!, flags);
        },
        setClipPlanes: (p: number, count: number) => {
            rec.clipPlanes = Array.from({length: count}, (_, i) => f32(p + i * 16, 4));
        },
        removeInstance: () => {},
        updateMorph: (g: number, p: number) => {
            rec.morphUpdates.set(g, [...(rec.morphUpdates.get(g) ?? []), f32(p, 1)[0]]);
        },
        setCamera: () => {},
        setBackground: () => {},
    } as unknown as WebRendererApi;
    return {module, renderer, rec};
}

function batched(): CustomBatchedMesh {
    // 4 triangles; ranges cover triangles 1 and 3, leaving gaps at 0 and 2.
    const pos = new Float32Array(12 * 3).map((_, i) => i * 0.1);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setIndex(new THREE.BufferAttribute(Uint32Array.from({length: 12}, (_, i) => i), 1));
    const ranges = new Map<string, [number, number]>([["a", [3, 3]], ["b", [9, 3]]]);
    return new CustomBatchedMesh(g, new THREE.MeshStandardMaterial(), ranges, "m", true, null);
}

function cameraAndScene(...objs: THREE.Object3D[]) {
    const scene = new THREE.Scene();
    objs.forEach((o) => scene.add(o));
    const camera = new THREE.PerspectiveCamera();
    camera.layers.enableAll();
    return {scene, camera};
}

test("batched mesh: draw ranges become segments, gaps included", () => {
    const {module, renderer, rec} = fakeModule();
    const mesh = batched();
    const {scene, camera} = cameraAndScene(mesh);
    new SceneSync(module, renderer).sync(scene, camera);
    const tri = [...rec.geometries.values()].find((g) => g.topology === Topology.Triangles)!;
    assert.deepEqual(tri.segments, [0, 3, 3, 3, 6, 3, 9, 3]);
});

test("hidden and selected ranges reach the renderer as segment state, only when they change", () => {
    const {module, renderer, rec} = fakeModule();
    const mesh = batched();
    const {scene, camera} = cameraAndScene(mesh);
    const sync = new SceneSync(module, renderer);
    sync.sync(scene, camera);
    mesh.hideBatchDrawRange(["a"]);
    mesh.updateSelectionGroups(["b"]);
    rec.states.clear();
    sync.sync(scene, camera);
    const state = [...rec.states.values()][0];
    // segments: gap, a, gap, b -> a hidden, b coloured (override alpha 255), gaps untouched
    assert.equal(state[3], SegmentFlag.Hidden);
    assert.equal(state[6] >>> 24, 255);
    // The selected segment is lit matte, as three.js' selection material is.
    assert.equal(state[7], SegmentFlag.Matte);
    assert.equal(state[1] | state[5], 0);

    rec.states.clear();
    sync.sync(scene, camera);
    assert.equal(rec.states.size, 0, "unchanged state is not re-sent");
});

test("a new position buffer version re-uploads; a colour change only updates colours", () => {
    const {module, renderer, rec} = fakeModule();
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(9), 3));
    const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({vertexColors: true}));
    const {scene, camera} = cameraAndScene(mesh);
    const sync = new SceneSync(module, renderer);
    sync.sync(scene, camera);
    (g.attributes.color as THREE.BufferAttribute).needsUpdate = true;
    sync.sync(scene, camera);
    assert.equal(rec.colorUpdates, 1);
    assert.equal(rec.removed.length, 0);
    (g.attributes.position as THREE.BufferAttribute).needsUpdate = true;
    sync.sync(scene, camera);
    assert.equal(rec.removed.length, 1);
});

test("objects removed from the scene are dropped from the renderer", () => {
    const {module, renderer, rec} = fakeModule();
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
    const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial());
    const {scene, camera} = cameraAndScene(mesh);
    const sync = new SceneSync(module, renderer);
    sync.sync(scene, camera);
    scene.remove(mesh);
    sync.sync(scene, camera);
    assert.equal(rec.removed.length, 1);
});

test("edge overlay: rangeId runs become line segments", () => {
    const {module, renderer, rec} = fakeModule();
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6 * 3), 3));
    g.setAttribute("rangeId", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 0, 1, 1]), 1));
    const tex = new THREE.DataTexture(new Uint8Array([255, 0]), 2, 1);
    const mat = new THREE.ShaderMaterial({uniforms: {uVisibleTex: {value: tex}}});
    const lines = new THREE.LineSegments(g, mat);
    const {scene, camera} = cameraAndScene(lines);
    new SceneSync(module, renderer).sync(scene, camera);
    const geom = [...rec.geometries.values()].find((x) => x.topology === Topology.Lines)!;
    assert.deepEqual(geom.segments, [0, 4, 4, 2]);
    const state = [...rec.states.values()][0];
    assert.deepEqual([state[1], state[3]], [0, SegmentFlag.Hidden]);
});

test("a warp swapped in as a new buffer reaches a wireframe that shares it (FEA step change)", () => {
    // applyField installs a fresh morph BufferAttribute on every step; the element-edge wireframe
    // shares the morph array with the mesh. A fresh attribute starts at the same version as the one
    // it replaces, so only its identity says it changed.
    const {module, renderer, rec} = fakeModule();
    const pos = new Float32Array(9);
    const meshGeom = new THREE.BufferGeometry();
    meshGeom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const mesh = new THREE.Mesh(meshGeom, new THREE.MeshBasicMaterial());
    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", new THREE.BufferAttribute(pos, 3)); // same array, own attribute
    lineGeom.setIndex([0, 1, 1, 2]);
    const lines = new THREE.LineSegments(lineGeom, new THREE.LineBasicMaterial());
    mesh.add(lines);
    const {scene, camera} = cameraAndScene(mesh);
    const sync = new SceneSync(module, renderer);
    sync.sync(scene, camera); // wireframe first seen before any result is applied

    const step = (value: number) => {
        meshGeom.morphAttributes.position = [new THREE.BufferAttribute(new Float32Array(9).fill(value), 3)];
        meshGeom.morphTargetsRelative = true;
        lineGeom.morphAttributes.position = meshGeom.morphAttributes.position;
        lineGeom.morphTargetsRelative = true;
        mesh.morphTargetInfluences = [1];
        lines.morphTargetInfluences = mesh.morphTargetInfluences;
        sync.sync(scene, camera);
    };
    step(1);
    step(2);
    const lineGeomId = [...rec.geometries.entries()].find(([, g]) => g.topology === Topology.Lines)![0];
    assert.deepEqual(rec.morphUpdates.get(lineGeomId), [1, 2]);
});

test("onBeforeRender hooks run before mirroring: an overlay that copies the influence there follows the warp", () => {
    // setSourceMorph gives the element wireframe its own morph and mirrors the mesh's influence in
    // onBeforeRender -- a hook three.js only calls when it draws, which in WebGPU mode it does not.
    const {module, renderer, rec} = fakeModule();
    const meshGeom = new THREE.BufferGeometry();
    meshGeom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
    meshGeom.morphAttributes.position = [new THREE.BufferAttribute(new Float32Array(9).fill(1), 3)];
    meshGeom.morphTargetsRelative = true;
    const mesh = new THREE.Mesh(meshGeom, new THREE.MeshBasicMaterial());
    mesh.updateMorphTargets();
    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
    lineGeom.morphAttributes.position = [new THREE.BufferAttribute(new Float32Array(9).fill(1), 3)];
    lineGeom.morphTargetsRelative = true;
    lineGeom.setIndex([0, 1, 1, 2]);
    const lines = new THREE.LineSegments(lineGeom, new THREE.LineBasicMaterial());
    lines.updateMorphTargets();
    lines.onBeforeRender = () => {
        lines.morphTargetInfluences![0] = mesh.morphTargetInfluences![0];
    };
    mesh.add(lines);
    const {scene, camera} = cameraAndScene(mesh);
    const sync = new SceneSync(module, renderer);
    mesh.morphTargetInfluences![0] = 0.75;
    sync.sync(scene, camera);
    const lineGeomId = [...rec.geometries.entries()].find(([, g]) => g.topology === Topology.Lines)![0];
    assert.equal(rec.influences.get(lineGeomId), 0.75);
});


test("section planes, polygonOffset overlays and sprites reach the renderer", () => {
    const {module, renderer, rec} = fakeModule();
    const sync = new SceneSync(module, renderer);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera();
    const tri = () => {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
        return g;
    };
    const plane = new THREE.Plane(new THREE.Vector3(1, 0, 0), -0.5);
    const cut = new THREE.Mesh(tri(), new THREE.MeshBasicMaterial({clippingPlanes: [plane]}));
    const overlay = new THREE.Mesh(tri(), new THREE.MeshBasicMaterial({
        polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -16,
    }));
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial());
    scene.add(cut, overlay, sprite);

    sync.sync(scene, camera);
    const all = [...rec.flags.values()];
    assert.equal(all.filter((f) => f & InstanceFlag.Clipped).length, 1, "only the mesh with clippingPlanes is cut");
    assert.equal(all.filter((f) => f & InstanceFlag.DepthOffset).length, 1, "only the polygonOffset overlay");
    assert.deepEqual(rec.clipPlanes, [[1, 0, 0, -0.5]]);
    // Sprites are not mirrored: they go to the three.js sprite pass, on its layer.
    assert.equal(rec.geometries.size, 2);
    assert.equal(sync.spriteCount, 1);
    assert.ok(sprite.layers.isEnabled(SPRITE_LAYER));

    // Unchanged planes are not re-sent; a moved plane is.
    rec.clipPlanes = null;
    sync.sync(scene, camera);
    assert.equal(rec.clipPlanes, null);
    plane.constant = 0.25;
    sync.sync(scene, camera);
    assert.deepEqual(rec.clipPlanes, [[1, 0, 0, 0.25]]);
});
