// `ctx.scene.contactBetween` (plugin API 1.27.0): where two objects of the loaded models touch, from
// the models' stored GLBs and the clash module's convex-contact columns.
import assert from "node:assert/strict";
import {test} from "node:test";

import {
    contactOfPair,
    makeContactBetween,
    pairSoup,
    type ContactColumns,
    type ContactGlbMembers,
    type SceneContactDeps,
} from "@/plugins/sceneContact";

/** A GLB's members: one box each, by name, two vertices (a diagonal) are enough for a hull here. */
function members(boxes: Record<string, [number[], number[]]>): ContactGlbMembers {
    const names = Object.keys(boxes);
    const vertices: number[] = [];
    const vertexOffsets = [0];
    const triangles: number[] = [];
    const triangleOffsets = [0];
    for (const name of names) {
        const [lo, hi] = boxes[name];
        vertices.push(...lo, ...hi);
        vertexOffsets.push(vertexOffsets[vertexOffsets.length - 1] + 2);
        triangles.push(0, 1, 0);
        triangleOffsets.push(triangleOffsets[triangleOffsets.length - 1] + 1);
    }
    return {ids: names.map((_, k) => String(k)), names, vertices, vertexOffsets, triangles, triangleOffsets};
}

/** The columns of one contact between meshes `i` and `j`: one patch, a square on the plane y = 1. */
function columns(i: number, j: number): ContactColumns {
    return {
        pairs: [i, j],
        near1: [0, 1, 0],
        near2: [0, 1.002, 0],
        normal: [0, 1, 0],
        penetrationDepth: [0.002],
        contactArea: [0.04],
        patchOffsets: [0, 1],
        patchNormals: [0, 1, 0],
        patchPointOffsets: [0, 4],
        patchPoints1: [-0.1, 1, -0.1, 0.1, 1, -0.1, 0.1, 1, 0.1, -0.1, 1, 0.1],
        patchPoints2: [-0.1, 1.002, -0.1, 0.1, 1.002, -0.1, 0.1, 1.002, 0.1, -0.1, 1.002, 0.1],
    };
}

const NONE: ContactColumns = {
    pairs: [], near1: [], near2: [], normal: [], penetrationDepth: [], contactArea: [],
    patchOffsets: [0], patchNormals: [], patchPointOffsets: [0], patchPoints1: [], patchPoints2: [],
};

function fakeDeps(over: Partial<SceneContactDeps> = {}) {
    const asked: string[] = [];
    const glbs: Record<string, ContactGlbMembers> = {
        "s/a.glb": members({beam: [[0, 0, 0], [1, 1, 1]], column: [[0, 1, 0], [1, 2, 1]]}),
        "s/b.glb": members({brace: [[5, 5, 5], [6, 6, 6]]}),
    };
    const deps: SceneContactDeps = {
        locate: (sourceName) => (sourceName === "file-model" ? {scope: "s", key: "a.glb"} : sourceName === "other" ? {scope: "s", key: "b.glb"} : null),
        scope: () => "s",
        getBytes: async (scope, key) => {
            asked.push(`${scope}/${key}`);
            if (!glbs[`${scope}/${key}`]) throw new Error("404");
            return new TextEncoder().encode(`${scope}/${key}`).buffer as ArrayBuffer;
        },
        glbMembers: async (glb) => glbs[new TextDecoder().decode(glb)],
        convexContacts: async () => columns(0, 1),
        ...over,
    };
    return {deps, asked};
}

test("pairSoup: the two meshes, the first object as mesh 0, offsets rebased, triangles local", () => {
    const m = members({x: [[0, 0, 0], [1, 1, 1]], y: [[2, 2, 2], [3, 3, 3]], z: [[4, 4, 4], [5, 5, 5]]});
    const soup = pairSoup({m, k: 2}, {m, k: 0});
    assert.deepEqual(Array.from(soup.vertices), [4, 4, 4, 5, 5, 5, 0, 0, 0, 1, 1, 1]);
    assert.deepEqual(Array.from(soup.vertexOffsets), [0, 2, 4]);
    assert.deepEqual(Array.from(soup.triangles), [0, 1, 0, 0, 1, 0]);
    assert.deepEqual(Array.from(soup.triangleOffsets), [0, 1, 2]);
});

test("contactOfPair: from the first object towards the second, whichever order the columns give", () => {
    const ab = contactOfPair(columns(0, 1))!;
    assert.deepEqual(ab.normal, [0, 1, 0]);
    assert.deepEqual(ab.nearA, [0, 1, 0]);
    assert.equal(ab.patches.length, 1);
    assert.deepEqual(ab.patches[0].pointsA.slice(0, 3), [-0.1, 1, -0.1]);
    assert.deepEqual(ab.patches[0].pointsB.slice(0, 3), [-0.1, 1.002, -0.1]);
    assert.equal(ab.area, 0.04);
    assert.equal(ab.distance, 0.002);

    const ba = contactOfPair(columns(1, 0))!;
    assert.deepEqual(ba.normal, [-0, -1, -0]);
    assert.deepEqual(ba.nearA, [0, 1.002, 0]);
    assert.deepEqual(ba.patches[0].pointsA.slice(0, 3), [-0.1, 1.002, -0.1]);
    assert.deepEqual(ba.patches[0].normal, [-0, -1, -0]);

    assert.equal(contactOfPair(NONE), null);
});

test("contactBetween: two objects of one model, and of two models, measured as one pair", async () => {
    let soupSeen: Float64Array | null = null;
    const {deps, asked} = fakeDeps({
        convexContacts: async (soup) => {
            soupSeen = soup.vertices;
            return columns(0, 1);
        },
    });
    const contactBetween = makeContactBetween(deps);
    const one = await contactBetween({sourceName: "file-model", name: "column"}, {sourceName: "file-model", name: "beam"});
    assert.ok("contact" in one && one.contact);
    // The column is mesh 0: what it was asked about first.
    assert.deepEqual(Array.from(soupSeen!).slice(0, 6), [0, 1, 0, 1, 2, 1]);
    const two = await contactBetween({sourceName: "file-model", name: "beam"}, {sourceName: "other", name: "brace"});
    assert.ok("contact" in two);
    // Each GLB is read once and kept.
    assert.deepEqual(asked, ["s/a.glb", "s/b.glb"]);
});

test("contactBetween: a model the caller loaded names its stored GLB", async () => {
    const {deps, asked} = fakeDeps();
    const answer = await makeContactBetween(deps)(
        {sourceName: "my-model", name: "beam", glb: {key: "a.glb"}},
        {sourceName: "my-model", name: "column", glb: {key: "a.glb", scope: "s"}},
    );
    assert.ok("contact" in answer && answer.contact);
    assert.deepEqual(asked, ["s/a.glb"]);
});

test("contactBetween: no contact is null; what cannot be measured says why", async () => {
    const apart = makeContactBetween(fakeDeps({convexContacts: async () => NONE}).deps);
    assert.deepEqual(await apart({sourceName: "file-model", name: "beam"}, {sourceName: "other", name: "brace"}), {contact: null});

    const contactBetween = makeContactBetween(fakeDeps().deps);
    const unknown = await contactBetween({sourceName: "dropped", name: "beam"}, {sourceName: "file-model", name: "beam"});
    assert.match((unknown as {unavailable: string}).unavailable, /dropped is not a GLB this viewer can read again/);
    const notIn = await contactBetween({sourceName: "file-model", name: "nope"}, {sourceName: "file-model", name: "beam"});
    assert.match((notIn as {unavailable: string}).unavailable, /nope is not an object of file-model/);
    const same = await contactBetween({sourceName: "file-model", name: "beam"}, {sourceName: "file-model", name: "beam"});
    assert.match((same as {unavailable: string}).unavailable, /one object/);
    const missing = await contactBetween({sourceName: "x", name: "beam", glb: {key: "gone.glb"}}, {sourceName: "file-model", name: "beam"});
    assert.match((missing as {unavailable: string}).unavailable, /could not be read: 404/);

    const failing = makeContactBetween(fakeDeps({convexContacts: async () => { throw new Error("no module"); }}).deps);
    const failed = await failing({sourceName: "file-model", name: "beam"}, {sourceName: "other", name: "brace"});
    assert.match((failed as {unavailable: string}).unavailable, /could not be measured: no module/);
});

test("contactBetween: a GLB that could not be read is asked for again next time", async () => {
    let fail = true;
    const {deps, asked} = fakeDeps();
    const getBytes = deps.getBytes;
    deps.getBytes = async (scope, key) => {
        if (fail) throw new Error("offline");
        return getBytes(scope, key);
    };
    const contactBetween = makeContactBetween(deps);
    const first = await contactBetween({sourceName: "file-model", name: "beam"}, {sourceName: "file-model", name: "column"});
    assert.ok("unavailable" in first);
    fail = false;
    const second = await contactBetween({sourceName: "file-model", name: "beam"}, {sourceName: "file-model", name: "column"});
    assert.ok("contact" in second && second.contact);
    assert.deepEqual(asked, ["s/a.glb"]);
});
