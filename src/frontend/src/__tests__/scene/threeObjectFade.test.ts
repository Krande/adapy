// Fading objects on the three.js viewport (`ViewportApi.setObjectFade`, utils/scene/threeObjectFade.ts):
// the models are found by their source, every range but the kept ones goes to each batched mesh's
// hidden slot drawn as a ghost, and what the user hid is put back as it was. And the adacpp WebGPU
// renderer that can draw the three.js scene draws those ranges see-through too, with a ghost
// instance, rather than not at all (a segment has no opacity of its own).
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import * as THREE from "three";

import {loadedSourceGroups} from "@/state/modelState";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {forgetThreeObjectFade, setThreeObjectFade} from "@/utils/scene/threeObjectFade";
import {SceneSync} from "@/viewport/adacpp/sceneSync";
import {InstanceFlag, SegmentFlag, type AdacppModule, type WebRendererApi} from "@/viewport/adacpp/types";
import {createThreeViewport} from "@/viewport/api/threeViewport";
import type {ObjectFade} from "@/viewport/api/objectFade";

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

const fade = (keep: Record<string, string[]>, opacity: number): ObjectFade => ({keep: new Map(Object.entries(keep).map(([k, v]) => [k, new Set(v)])), opacity});
const hidden = (m: CustomBatchedMesh) => [...m.getHiddenRanges()].sort();
const IDS = ["e0", "e1", "e2", "e3"];

beforeEach(() => {
    setThreeObjectFade(null);
    loadedSourceGroups.clear();
});

test("the viewport fades the rest of each model it names, and puts every one back", () => {
    const viewport = createThreeViewport({} as never);
    const a = load("models/a.glb", IDS);
    const b = load("models/b.glb", IDS);
    const c = load("models/c.glb", IDS);
    b.hideBatchDrawRange(["e3"]);

    viewport.setObjectFade!(fade({"models/a.glb": ["e1"], "models/b.glb": []}, 0.2));
    assert.deepEqual(hidden(a), ["e0", "e2", "e3"]);
    assert.deepEqual(hidden(b), IDS, "an empty set fades the model whole");
    assert.deepEqual(hidden(c), [], "a model not named is left alone");
    for (const m of [a, b]) assert.ok(Math.abs(m.hiddenGhostOpacity()! - 0.2) < 1e-6, "drawn as a ghost, not hidden");
    assert.equal(c.hiddenGhostOpacity(), null);

    // Another focus: the previous fade is put back first, so nothing accumulates.
    viewport.setObjectFade!(fade({"models/a.glb": ["e0", "e3"]}, 0.4));
    assert.deepEqual(hidden(a), ["e1", "e2"]);
    assert.deepEqual(hidden(b), ["e3"], "the user's own hiding is back");
    assert.equal(b.hiddenGhostOpacity(), null);

    viewport.setObjectFade!(null);
    assert.deepEqual(hidden(a), []);
    assert.equal(a.hiddenGhostOpacity(), null, "the hidden slot draws nothing again");
});

test("what the user hid stays out of a fade; opacity 0 hides the rest instead", () => {
    const a = load("models/a.glb", IDS);
    a.hideBatchDrawRange(["e1"]);
    setThreeObjectFade(fade({"models/a.glb": ["e1", "e2"]}, 0));
    assert.deepEqual(hidden(a), ["e0", "e1", "e3"], "a hidden member is not brought back");
    assert.equal(a.hiddenGhostOpacity(), null);
    setThreeObjectFade(null);
    assert.deepEqual(hidden(a), ["e1"]);
});

test("a model cleared under a fade is forgotten, not touched", () => {
    const a = load("models/a.glb", IDS);
    setThreeObjectFade(fade({"models/a.glb": ["e1"]}, 0.2));
    a.dispose();
    forgetThreeObjectFade();
    setThreeObjectFade(null); // nothing left to put back
    // And a mesh disposed without being forgotten does not stop the rest being put back.
    const b = load("models/b.glb", IDS);
    const c = load("models/c.glb", IDS);
    setThreeObjectFade(fade({"models/b.glb": ["e1"], "models/c.glb": ["e2"]}, 0.2));
    b.dispose();
    setThreeObjectFade(null);
    assert.deepEqual(hidden(c), []);
});

test("the WebGPU mirror draws a faded range see-through with a ghost instance, and drops it after", () => {
    const heap = new ArrayBuffer(1 << 20);
    let top = 16, nextInst = 0;
    const module = {
        HEAPU8: new Uint8Array(heap), HEAPU32: new Uint32Array(heap), HEAPF32: new Float32Array(heap),
        _malloc: (n: number) => ((top += (n + 15) & ~15), top - ((n + 15) & ~15)),
        _free: () => {},
    } as unknown as AdacppModule;
    const states = new Map<number, number[]>();
    const instances = new Map<number, {geometry: number; opacity?: number; flags?: number}>();
    const renderer = {
        addGeometry: () => 7,
        addInstance: (geometry: number) => (instances.set(nextInst, {geometry}), nextInst++),
        setInstance: (i: number, _m: number, _r: number, _g: number, _b: number, a: number, flags: number) => Object.assign(instances.get(i)!, {opacity: a, flags}),
        setSegmentStates: (i: number, p: number, n: number) => void states.set(i, Array.from(module.HEAPU32.subarray(p >> 2, (p >> 2) + n * 2))),
        removeInstance: (i: number) => void instances.delete(i),
        removeGeometry: () => {},
        setClipPlanes: () => {},
        setCamera: () => {},
        setBackground: () => {},
        setLighting: () => {},
    } as unknown as WebRendererApi;
    const a = load("models/a.glb", ["e0", "e1", "e2"]);
    const scene = a.parent!.parent as THREE.Scene;
    const camera = new THREE.PerspectiveCamera();
    camera.layers.enableAll();
    const sync = new SceneSync(module, renderer);
    const picture = (i: number) => (states.get(i) ?? []).filter((_, k) => k % 2 === 1).map((f) => (f & SegmentFlag.Hidden ? "hidden" : "drawn")).join(" ");

    sync.sync(scene, camera);
    assert.equal(instances.size, 1);
    setThreeObjectFade(fade({"models/a.glb": ["e1"]}, 0.25));
    sync.sync(scene, camera);
    assert.equal(instances.size, 2, "the mesh, and its ghost");
    const [main, ghost] = [...instances.keys()];
    assert.equal(instances.get(ghost)!.geometry, instances.get(main)!.geometry, "of the same geometry");
    assert.ok(Math.abs(instances.get(ghost)!.opacity! - 0.25) < 1e-6);
    assert.ok(instances.get(ghost)!.flags! & InstanceFlag.Transparent);
    assert.ok(instances.get(ghost)!.flags! & InstanceFlag.NoPick, "a click goes to what is solid");
    assert.equal(picture(main), "hidden drawn hidden");
    assert.equal(picture(ghost), "drawn hidden drawn");

    // "Hide rest": no ghost; the fade taken away: none either, and everything drawn.
    setThreeObjectFade(fade({"models/a.glb": ["e1"]}, 0));
    sync.sync(scene, camera);
    assert.deepEqual([...instances.keys()], [main]);
    setThreeObjectFade(fade({"models/a.glb": ["e1"]}, 0.25));
    sync.sync(scene, camera);
    setThreeObjectFade(null);
    sync.sync(scene, camera);
    assert.deepEqual([...instances.keys()], [main]);
    assert.equal(picture(main), "drawn drawn drawn");
});
