// Placing a loaded model's nodes from outside (viewport/api/modelPose.ts): the pose a caller holds,
// the three.js viewport's adapter, and the three.js-free viewport's arithmetic and the file it reads.
import {test} from "node:test";
import assert from "node:assert/strict";
import {Group, Matrix4, Object3D, Scene} from "three";

import {makeGlbPosable, REST_ANIMATION} from "@/utils/glb/glbPosable";
import {fromGltfNode, identity4, invert4, multiply4} from "@/viewport/api/mat4";
import {createModelPose, refreshModelPoses, type ModelNodePoser} from "@/viewport/api/modelPose";
import {posedNodesUnder} from "@/viewport/api/threeModelPose";
import {setActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";
import {placeInstance, planNodePlacement} from "@/viewport/native/nodePlacement";

const close = (a: ArrayLike<number>, b: ArrayLike<number>, eps = 1e-5): void => {
    assert.equal(a.length, b.length);
    for (let i = 0; i < a.length; i++) assert.ok(Math.abs(a[i] - b[i]) <= eps, `at ${i}: ${a[i]} is not ${b[i]}`);
};

/** A turn about z by `deg` at `xyz`, column-major. */
const pose = (xyz: [number, number, number], deg = 0): number[] => {
    const c = Math.cos((deg * Math.PI) / 180), s = Math.sin((deg * Math.PI) / 180);
    return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, xyz[0], xyz[1], xyz[2], 1];
};

// ----- the matrices ---------------------------------------------------------------------------

test("mat4: a product and an inverse are three.js's", () => {
    const a = pose([1, 2, 3], 30), b = pose([-4, 0.5, 2], -75);
    const want = new Matrix4().fromArray(a).multiply(new Matrix4().fromArray(b));
    close(multiply4(new Float64Array(16), a, b), want.elements);
    const inverse = new Float64Array(16);
    assert.equal(invert4(inverse, want.elements), true);
    close(inverse, want.clone().invert().elements);
    close(multiply4(new Float64Array(16), inverse, want.elements), identity4());
    assert.equal(invert4(inverse, new Float64Array(16)), false);
    // In place, and from the middle of an array.
    const packed = new Float64Array(32);
    packed.set(a, 16);
    const out = new Float64Array(a);
    close(multiply4(out, out, packed, 0, 16), new Matrix4().fromArray(a).multiply(new Matrix4().fromArray(a)).elements);
});

test("mat4: a glTF node's translation, rotation and scale as one matrix", () => {
    const s = Math.SQRT1_2;
    const node = {translation: [1, 2, 3], rotation: [0, 0, s, s], scale: [2, 1, 1]};
    // A quarter turn about z of an x axis twice as long: x goes to 2y.
    close(fromGltfNode(node), [0, 2, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 1, 2, 3, 1]);
    close(fromGltfNode({matrix: pose([4, 5, 6], 10)}), pose([4, 5, 6], 10));
    close(fromGltfNode({}), identity4());
});

// ----- the file -------------------------------------------------------------------------------

function glb(json: object, bin = new Uint8Array(0)): Uint8Array {
    const text = new TextEncoder().encode(JSON.stringify(json));
    const jsonLength = (text.byteLength + 3) & ~3, binLength = (bin.byteLength + 3) & ~3;
    const out = new Uint8Array(12 + 8 + jsonLength + (binLength ? 8 + binLength : 0));
    const view = new DataView(out.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, out.byteLength, true);
    view.setUint32(12, jsonLength, true);
    view.setUint32(16, 0x4e4f534a, true);
    out.fill(0x20, 20, 20 + jsonLength);
    out.set(text, 20);
    if (binLength) {
        view.setUint32(20 + jsonLength, binLength, true);
        view.setUint32(24 + jsonLength, 0x004e4942, true);
        out.set(bin, 28 + jsonLength);
    }
    return out;
}

function read(bytes: Uint8Array): {json: any; bin: Uint8Array} {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    assert.equal(view.getUint32(8, true), bytes.byteLength, "the header's length is the file's");
    const jsonLength = view.getUint32(12, true);
    const json = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonLength)));
    const binLength = view.getUint32(20 + jsonLength, true);
    assert.equal(view.getUint32(24 + jsonLength, true), 0x004e4942);
    assert.equal(28 + jsonLength + binLength, bytes.byteLength);
    return {json, bin: bytes.subarray(28 + jsonLength, 28 + jsonLength + binLength)};
}

/** An arm on a base: `arm` hangs on `base` and draws through `arm_visual`; `fixed` has a matrix. */
const RIG = {
    asset: {version: "2.0"},
    scene: 0,
    scenes: [{nodes: [0, 3]}],
    nodes: [
        {name: "base", translation: [1, 0, 0], children: [1]},
        {name: "arm", translation: [0, 2, 0], rotation: [0, 0, Math.SQRT1_2, Math.SQRT1_2], children: [2]},
        {name: "arm_visual", translation: [0, 0, 0.5], mesh: 0},
        {name: "fixed", matrix: pose([9, 9, 9])},
    ],
    buffers: [{byteLength: 6}],
    bufferViews: [{buffer: 0, byteOffset: 0, byteLength: 6}],
    accessors: [],
};

test("a posable GLB: every node is named by an animation that moves none, and the file is whole", () => {
    const made = makeGlbPosable(glb(RIG, new Uint8Array([1, 2, 3, 4, 5, 6])));
    assert.ok(made);
    assert.deepEqual(made.names, ["base", "arm", "arm_visual", "fixed"]);
    assert.deepEqual([...made.parents], [-1, 0, 1, -1]);
    assert.deepEqual(made.apart, [true, true, true, false]);
    // Where each rests in the file's coordinates: the arm a quarter turn at (1, 2, 0), its visual above it.
    close(made.rest.subarray(0, 16), pose([1, 0, 0]));
    close(made.rest.subarray(16, 32), pose([1, 2, 0], 90));
    close(made.rest.subarray(32, 48), pose([1, 2, 0.5], 90));
    close(made.rest.subarray(48, 64), pose([9, 9, 9]));

    const {json, bin} = read(made.bytes);
    assert.deepEqual([...bin.subarray(0, 6)], [1, 2, 3, 4, 5, 6], "what the file had is where it was");
    assert.equal(json.buffers[0].byteLength, bin.byteLength);
    const animation = json.animations.at(-1);
    assert.equal(animation.name, REST_ANIMATION);
    assert.deepEqual(animation.channels.map((c: any) => [c.target.node, c.target.path]), [[0, "translation"], [1, "translation"], [2, "translation"]]);
    // Each channel holds its node's own translation at both of its two times: nothing moves.
    const floats = (accessor: any): number[] => {
        const view = json.bufferViews[accessor.bufferView];
        const at = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
        const count = accessor.count * (accessor.type === "VEC3" ? 3 : 1);
        return [...new Float32Array(bin.slice(at, at + count * 4).buffer)];
    };
    const values = animation.channels.map((c: any) => floats(json.accessors[animation.samplers[c.sampler].output]));
    assert.deepEqual(values, [[1, 0, 0, 1, 0, 0], [0, 2, 0, 0, 2, 0], [0, 0, 0.5, 0, 0, 0.5]]);
    assert.deepEqual(floats(json.accessors[animation.samplers[0].input]), [0, 1]);
});

test("a posable GLB: one without a binary chunk gets one; what is not a GLB is refused", () => {
    const made = makeGlbPosable(glb({asset: {version: "2.0"}, nodes: [{name: "only"}]}));
    assert.ok(made);
    const {json, bin} = read(made.bytes);
    assert.equal(json.buffers[0].byteLength, bin.byteLength);
    assert.equal(json.animations[0].channels.length, 1);
    assert.equal(makeGlbPosable(new Uint8Array(40)), null);
    assert.equal(makeGlbPosable(glb({asset: {version: "2.0"}})), null);
});

// ----- the three.js-free viewport's arithmetic --------------------------------------------------

test("native: a node is the instances below it, each kept where it is on the node", () => {
    const made = makeGlbPosable(glb(RIG, new Uint8Array(6)))!;
    // The renderer draws 100 further along x than the file says (the scene's recentring).
    const frame = pose([100, 0, 0]);
    const rest = (node: number): Float64Array => multiply4(new Float64Array(16), frame, made.rest, 0, node * 16) as Float64Array;
    // Instance 7 draws the arm's visual (node 2), instance 8 the fixed node (3).
    const plan = planNodePlacement(made, ["arm", "nothing", "fixed"], [7, 2, 8, 3], (instance) => rest(instance === 7 ? 2 : 3));
    assert.deepEqual(plan.found, [true, false, false], "a node the file places by a matrix is not kept apart");
    assert.deepEqual(plan.driven.map((d) => [d.instance, d.index]), [[7, 0], [8, 2]]);

    const matrices = new Float32Array(48);
    matrices.set(pose([0, 0, 3], 180), 0); // the arm, turned round at (0, 0, 3)
    const out = new Float64Array(16);
    assert.equal(placeInstance(plan.driven[0], matrices, out), true);
    // The visual is 0.5 up the arm: the renderer has it at (100, 0, 3.5), turned as the arm is.
    close(out, pose([100, 0, 3.5], 180));
    // Zeros hide.
    matrices.fill(0, 0, 16);
    placeInstance(plan.driven[0], matrices, out);
    close(out, new Float64Array(16));
    assert.equal(placeInstance(plan.driven[1], new Float32Array(16), out), false, "no matrix for the third name");
});

test("native: a node placed by its nearest named ancestor, and by itself when it is named", () => {
    const made = makeGlbPosable(glb(RIG, new Uint8Array(6)))!;
    const rest = (node: number): Float64Array => made.rest.slice(node * 16, node * 16 + 16);
    const byBase = planNodePlacement(made, ["base"], [7, 2], () => rest(2));
    assert.deepEqual(byBase.driven.map((d) => d.index), [0]);
    const out = new Float64Array(16);
    // The base at rest: everything on it is where it rests.
    placeInstance(byBase.driven[0], new Float32Array(pose([1, 0, 0])), out);
    close(out, rest(2));
    const both = planNodePlacement(made, ["base", "arm"], [7, 2], () => rest(2));
    assert.deepEqual(both.driven.map((d) => d.index), [1], "the arm's own name is nearer than the base's");
});

// ----- the three.js viewport's adapter ---------------------------------------------------------

test("three: a node is the object the file names, placed in the file's coordinates", () => {
    const scene = new Scene();
    const root = new Group(); // the loaded scene: recentred, and turned upright
    root.position.set(10, 0, 0);
    root.rotation.set(Math.PI / 2, 0, 0);
    const base = new Object3D();
    base.name = "base_1"; // the loader's unique name; the file's is kept beside it
    base.userData.name = "base";
    base.position.set(1, 0, 0);
    const arm = new Object3D();
    arm.name = "arm";
    const visual = new Object3D();
    visual.position.set(0, 0, 0.5);
    arm.add(visual);
    base.add(arm);
    root.add(base);
    scene.add(root);

    const poser = posedNodesUnder(root, ["arm", "base", "missing"]);
    assert.deepEqual(poser.found, [true, true, false]);
    assert.equal(poser.alive(), true);
    const matrices = new Float32Array(48);
    matrices.set(pose([0, 0, 3], 90), 0);
    matrices.set(pose([5, 5, 0]), 16);
    poser.place(matrices);
    scene.updateMatrixWorld(true);
    // The arm is where it was put in the file's coordinates, whatever its parent was moved to.
    const want = root.matrixWorld.clone().multiply(new Matrix4().fromArray(pose([0, 0, 3], 90)));
    close(arm.matrixWorld.elements, want.elements);
    close(base.matrixWorld.elements, root.matrixWorld.clone().multiply(new Matrix4().fromArray(pose([5, 5, 0]))).elements);
    // What hangs on it went with it.
    close(visual.matrixWorld.elements, want.clone().multiply(new Matrix4().makeTranslation(0, 0, 0.5)).elements);

    matrices.fill(0, 0, 16);
    poser.place(matrices);
    assert.equal(arm.visible, false);
    assert.equal(base.visible, true);
    matrices.set(pose([0, 0, 0]), 0);
    poser.place(matrices);
    assert.equal(arm.visible, true);

    scene.remove(root);
    assert.equal(poser.alive(), false);
});

// ----- the pose a caller holds ------------------------------------------------------------------

function viewportWith(models: Map<string, string[]>, log: string[]): ViewportApi {
    return {
        modelNodes: (sourceName: string, names: readonly string[]): ModelNodePoser | null => {
            const nodes = models.get(sourceName);
            if (!nodes) return null;
            return {
                found: names.map((name) => nodes.includes(name)),
                alive: () => models.get(sourceName) === nodes,
                place: (matrices) => void log.push(`${sourceName}:${matrices[12]}`),
            };
        },
    } as unknown as ViewportApi;
}

test("a pose outlives the model and the viewport: each new one is placed as the last was", () => {
    const log: string[] = [];
    const models = new Map<string, string[]>();
    const withdraw = setActiveViewport(viewportWith(models, log));
    const held = createModelPose("rig", ["arm"]);
    assert.equal(held.found(), null);
    held.place(new Float32Array(pose([1, 0, 0])));
    assert.deepEqual(log, [], "no model yet: nothing to place");

    models.set("rig", ["arm"]);
    refreshModelPoses();
    assert.deepEqual(log, ["rig:1"], "the model arrived: it is placed as asked before");
    assert.deepEqual(held.found(), [true]);
    held.place(new Float32Array(pose([2, 0, 0])));
    refreshModelPoses();
    assert.deepEqual(log, ["rig:1", "rig:2"], "the same model is not placed again");

    // The model is loaded again (a fuller one under the same name).
    models.set("rig", ["arm", "amr"]);
    refreshModelPoses();
    assert.deepEqual(log, ["rig:1", "rig:2", "rig:2"]);

    // Another viewport takes the place of this one: the pose follows by itself.
    const other: string[] = [];
    const withdrawOther = setActiveViewport(viewportWith(new Map([["rig", ["arm"]]]), other));
    assert.deepEqual(other, ["rig:2"]);

    held.remove();
    held.place(new Float32Array(pose([3, 0, 0])));
    refreshModelPoses();
    assert.deepEqual(other, ["rig:2", "rig:3"], "removed, it is no longer kept in place, but may still be placed");
    withdrawOther();
    withdraw();
});
