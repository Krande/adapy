// Box selection behind the viewport interface: the rules that are plain numbers (a box or a screen
// rectangle against an object's bounds, the handles' maths), and applying a box through whichever
// viewport is on screen.
import {test} from "node:test";
import assert from "node:assert/strict";

import {useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {addBoxAroundSelection, applyBoxSelection, bindActiveBoxSelection, selectInScreenRect} from "@/utils/bboxSelection/applyBoxSelection";
import {axisVector, boxEdges, boxFaces, lineParamAtScreen, minBoxSize, moveArrows, screenPointOverBox, toScreen} from "@/utils/bboxSelection/boxHandles";
import {containedAt, overlapsAt, triangleTouchesBox, triangleTouchesBoxAt, type Aabb, type BoxSelectMode, type Vec3} from "@/utils/bboxSelection/boxMath";
import {rectAgainstBounds, rectTakesBounds, RectVsBounds, type NdcRect, type ScreenBoxMode} from "@/utils/bboxSelection/screenRect";
import type {ObjectSet} from "@/viewport/api/boxSelection";
import {setActiveViewport, type ObjectRef, type SetSelectionOptions, type ViewportApi} from "@/viewport/api/viewportApi";
import {KeySet} from "@/viewport/native/nativeBoxSelection";
import {objectKey} from "@/viewport/native/nativeScene";

const unit: Aabb = {min: [0, 0, 0], max: [1, 1, 1]};

/** A camera at (0, 0, 5) looking down -z: perspective, 90 degrees, square view (column-major). */
function camera(): number[] {
    const near = 0.1, far = 100;
    const proj = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0];
    const view = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -5, 1];
    const out = new Array<number>(16).fill(0);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) out[c * 4 + r] += proj[k * 4 + r] * view[c * 4 + k];
    return out;
}

// --- a triangle against a box, without making anything per call ---

/** The separating-axis test as it was first written, on arrays: the reference the plain-number one must match. */
function referenceTouches(a: Vec3, b: Vec3, c: Vec3, box: Aabb): boolean {
    const cross = (p: Vec3, q: Vec3): Vec3 => [p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0]];
    const dot = (p: Vec3, q: Vec3) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
    const ctr: Vec3 = [(box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2];
    const h: Vec3 = [(box.max[0] - box.min[0]) / 2, (box.max[1] - box.min[1]) / 2, (box.max[2] - box.min[2]) / 2];
    const v = [a, b, c].map((p): Vec3 => [p[0] - ctr[0], p[1] - ctr[1], p[2] - ctr[2]]);
    const e: Vec3[] = [0, 1, 2].map((i): Vec3 => [v[(i + 1) % 3][0] - v[i][0], v[(i + 1) % 3][1] - v[i][1], v[(i + 1) % 3][2] - v[i][2]]);
    for (let k = 0; k < 3; k++) {
        if (Math.min(v[0][k], v[1][k], v[2][k]) > h[k] || Math.max(v[0][k], v[1][k], v[2][k]) < -h[k]) return false;
    }
    const n = cross(e[0], e[1]);
    if (Math.abs(dot(n, v[0])) > h[0] * Math.abs(n[0]) + h[1] * Math.abs(n[1]) + h[2] * Math.abs(n[2])) return false;
    for (const edge of e) {
        for (let k = 0; k < 3; k++) {
            const axis = cross(edge, axisVector(k));
            const p = v.map((q) => dot(axis, q));
            const rad = h[0] * Math.abs(axis[0]) + h[1] * Math.abs(axis[1]) + h[2] * Math.abs(axis[2]);
            if (Math.min(...p) > rad || Math.max(...p) < -rad) return false;
        }
    }
    return true;
}

test("the plain-number triangle test answers as the one on arrays, for triangles all around a box", () => {
    // A small generator with a fixed seed: the same triangles every run.
    let seed = 12345;
    const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296) * 6 - 2.5;
    const box: Aabb = {min: [-0.5, 0, 0.25], max: [1, 1.5, 2]};
    let touching = 0;
    for (let i = 0; i < 20000; i++) {
        const a: Vec3 = [random(), random(), random()], b: Vec3 = [random(), random(), random()], c: Vec3 = [random(), random(), random()];
        const expected = referenceTouches(a, b, c, box);
        assert.equal(triangleTouchesBoxAt(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2], box), expected, `triangle ${i}`);
        assert.equal(triangleTouchesBox(a, b, c, box), expected);
        if (expected) touching++;
    }
    assert.ok(touching > 2000 && touching < 18000, `${touching} of 20000 touch: the sample must hold both answers`);
});

// --- a screen rectangle against an object's bounds ---

const whole: NdcRect = {x0: -1, y0: -1, x1: 1, y1: 1};
const bounds = (...boxes: number[][]) => new Float32Array(boxes.flat());

test("a rectangle against a box's picture: inside, outside, straddling, behind the camera", () => {
    const m = camera();
    const b = bounds([-1, -1, -1, 1, 1, 1], [20, -1, -1, 22, 1, 1], [-1, -1, -1, 1, 1, 9]);
    assert.equal(rectAgainstBounds(b, 0, m, whole), RectVsBounds.Inside);
    assert.equal(rectAgainstBounds(b, 6, m, whole), RectVsBounds.Outside);
    assert.equal(rectAgainstBounds(b, 0, m, {x0: 0, y0: 0, x1: 1, y1: 1}), RectVsBounds.Straddles);
    assert.equal(rectAgainstBounds(b, 12, m, whole), RectVsBounds.Behind);
});

test("by bounds alone a window takes what is wholly inside, a crossing what touches", () => {
    const m = camera();
    const b = bounds([-1, -1, -1, 1, 1, 1], [20, -1, -1, 22, 1, 1], [-1, -1, -1, 1, 1, 9]);
    const quarter: NdcRect = {x0: 0, y0: 0, x1: 1, y1: 1};
    const taken = (rect: NdcRect, mode: ScreenBoxMode) => [0, 1, 2].filter((i) => rectTakesBounds(b, i * 6, m, rect, mode));
    assert.deepEqual(taken(whole, "window"), [0]);
    // The third box reaches behind the camera: never in a window, in a crossing by its part in front.
    assert.deepEqual(taken(whole, "crossing"), [0, 2]);
    assert.deepEqual(taken(quarter, "window"), []);
    assert.deepEqual(taken(quarter, "crossing"), [0, 2]);
    assert.deepEqual(taken({x0: -1, y0: -1, x1: -0.9, y1: -0.9}, "crossing"), []);
});

// --- the handles ---

test("a box has twelve edges, each along one axis between two of its corners", () => {
    const e = boxEdges({min: [0, 0, 0], max: [1, 2, 3]});
    assert.equal(e.length, 72);
    const lengths = [0, 0, 0];
    for (let i = 0; i < 72; i += 6) {
        const d = [e[i + 3] - e[i], e[i + 4] - e[i + 1], e[i + 5] - e[i + 2]];
        const axis = d.findIndex((x) => x !== 0);
        assert.equal(d.filter((x) => x !== 0).length, 1, "an edge runs along exactly one axis");
        assert.equal(d[axis], [1, 2, 3][axis]);
        lengths[axis]++;
    }
    assert.deepEqual(lengths, [4, 4, 4]);
});

test("a box's faces are twelve triangles over its eight corners, two per face", () => {
    const {points, indices} = boxFaces({min: [0, 0, 0], max: [1, 2, 3]});
    assert.equal(points.length, 24);
    assert.equal(indices.length, 36);
    const perFace = new Map<string, number>();
    for (let t = 0; t < 36; t += 3) {
        const corners = [indices[t], indices[t + 1], indices[t + 2]].map((c) => [points[c * 3], points[c * 3 + 1], points[c * 3 + 2]]);
        // The three corners of a face's triangle share one coordinate: the face's.
        const axis = [0, 1, 2].find((k) => corners.every((c) => c[k] === corners[0][k]));
        assert.ok(axis !== undefined, `triangle ${t / 3} lies in no face`);
        const face = `${axis}:${corners[0][axis!]}`;
        perFace.set(face, (perFace.get(face) ?? 0) + 1);
    }
    assert.deepEqual([...perFace].sort(), [["0:0", 2], ["0:1", 2], ["1:0", 2], ["1:2", 2], ["2:0", 2], ["2:3", 2]]);
});

test("the pointer over a point of a line gives that point's place along it, in perspective", () => {
    const m = camera();
    // A line that runs away from the camera at a slant, so equal steps along it are unequal on screen.
    const origin: Vec3 = [-1, 0.5, 1], dir: Vec3 = [1, 0.25, -1.5];
    for (const t of [-0.5, 0, 0.75, 2.5]) {
        const p = toScreen(m, [origin[0] + t * dir[0], origin[1] + t * dir[1], origin[2] + t * dir[2]])!;
        const got = lineParamAtScreen(m, origin, dir, p.x, p.y, 1);
        assert.ok(got !== null && Math.abs(got - t) < 1e-9, `t = ${t}: got ${got}`);
    }
    // Off the line, the nearest point of it on the screen answers: beside the point at t = 1, at right
    // angles to the line's picture, the answer is still 1.
    const a = toScreen(m, origin)!, b = toScreen(m, [origin[0] + dir[0], origin[1] + dir[1], origin[2] + dir[2]])!;
    const length = Math.hypot(b.x - a.x, b.y - a.y);
    const beside = lineParamAtScreen(m, origin, dir, b.x - ((b.y - a.y) / length) * 0.1, b.y + ((b.x - a.x) / length) * 0.1, 1);
    assert.ok(beside !== null && Math.abs(beside - 1) < 1e-9, `beside the line: ${beside}`);
});

test("a wider view is measured in pixels' proportions, and a line seen end on has no answer", () => {
    const m = camera();
    const origin: Vec3 = [0, 0, 0], dir: Vec3 = [1, 1, 0];
    const p = toScreen(m, [1.5, 1.5, 0])!;
    // The camera's matrix is for a square view; drawn twice as wide, a pointer on the line's picture
    // is still that point of the line.
    const onLine = lineParamAtScreen(m, origin, dir, p.x, p.y, 2);
    assert.ok(onLine !== null && Math.abs(onLine - 1.5) < 1e-9);
    assert.equal(lineParamAtScreen(m, [0, 0, 0], [0, 0, 1], 0.2, 0.2, 1), null);
});

test("a point on the screen is over a box when it is over one of its faces' pictures", () => {
    const m = camera();
    assert.ok(screenPointOverBox(m, unit, 0.1, 0.1));
    assert.ok(!screenPointOverBox(m, unit, -0.2, 0.1));
    assert.ok(!screenPointOverBox(m, unit, 0.9, 0.9));
    // A box that reaches behind the camera has no picture to be over.
    assert.ok(!screenPointOverBox(m, {min: [-1, -1, -1], max: [1, 1, 9]}, 0, 0));
});

test("the move arrows start at the box's centre, one along each axis; a box cannot be dragged flat", () => {
    const arrows = moveArrows({min: [0, 0, 0], max: [2, 4, 6]}, 0.5);
    assert.deepEqual(arrows.map((a) => a.base), [[1, 2, 3], [1, 2, 3], [1, 2, 3]]);
    assert.deepEqual(arrows.map((a) => a.tip), [[1.5, 2, 3], [1, 2.5, 3], [1, 2, 3.5]]);
    assert.equal(minBoxSize({min: [0, 0, 0], max: [2000, 1, 1]}), 2);
    assert.equal(minBoxSize(unit), 1e-3);
});

// --- applying a box through the viewport on screen ---

/** A viewport of objects known only by their boxes, as the three.js-free one is: it records what
 *  was selected and how. */
function boundsViewport(objects: Record<string, number[]>) {
    const ids = Object.keys(objects);
    const b = new Float32Array(ids.flatMap((id) => objects[id]));
    let selected: string[] = [];
    const calls: {ids: string[]; options: SetSelectionOptions}[] = [];
    const set = (keys: string[]): ObjectSet => new KeySet(keys.map((id) => objectKey("m", id)));
    const api = {
        kind: "native",
        // Nothing here is undone: the history is told that no action changes anything.
        captureState: () => ({selected: 0}),
        stateChange: () => ({selection: false, visibility: false, hidden: 0, shown: 0, after: () => ({selected: 0})}),
        restoreState: () => {},
        objectsInBox: (box: Aabb, mode: BoxSelectMode) => set(ids.filter((_, i) => (mode === "full" ? containedAt : overlapsAt)(b, i, box))),
        objectsInScreenRect: (rect: NdcRect, mode: ScreenBoxMode) => set(ids.filter((_, i) => rectTakesBounds(b, i * 6, camera(), rect, mode))),
        selectionBounds: (): Aabb | null => {
            const own = selected.map((id) => objects[id]);
            if (!own.length) return null;
            return {
                min: [0, 1, 2].map((k) => Math.min(...own.map((o) => o[k]))) as Vec3,
                max: [0, 1, 2].map((k) => Math.max(...own.map((o) => o[k + 3]))) as Vec3,
            };
        },
        setSelection: (refs: Iterable<ObjectRef>, options: SetSelectionOptions = {}) => {
            // A ref is only good while it is being read: copy what is needed.
            const taken = Array.from(refs, (r) => r.id);
            selected = options.add ? [...new Set([...selected, ...taken])] : taken;
            options.within?.();
            calls.push({ids: taken, options});
            return taken.length;
        },
    } as unknown as ViewportApi;
    return {api, calls, selected: () => selected, select: (...which: string[]) => void (selected = which)};
}

const OBJECTS = {inside: [0.2, 0.2, 0.2, 0.8, 0.8, 0.8], straddling: [0.5, 0.5, 0.5, 3, 0.9, 0.9], far: [10, 10, 10, 11, 11, 11]};

function reset(): void {
    useBBoxSelectionStore.setState({boxes: [], activeId: null, menu: null});
    useObjectInfoStore.getState().setName(null);
}

test("a box selects what it touches, or only what it holds, on the viewport on screen", () => {
    reset();
    const v = boundsViewport(OBJECTS);
    const withdraw = setActiveViewport(v.api);
    try {
        const id = useBBoxSelectionStore.getState().add(unit);
        assert.equal(applyBoxSelection(id), 2);
        assert.deepEqual(v.selected(), ["inside", "straddling"]);
        assert.equal(v.calls[0].options.coalesce, `box:${id}`, "a drag's re-applied selections are one undo step");
        assert.match(useObjectInfoStore.getState().name ?? "", /: 2 elements$/);

        useBBoxSelectionStore.getState().update(id, {mode: "full"});
        assert.equal(applyBoxSelection(id), 1);
        assert.deepEqual(v.selected(), ["inside"]);
        assert.match(useObjectInfoStore.getState().name ?? "", /: 1 element$/);
        assert.equal(applyBoxSelection("no such box"), 0);
    } finally {
        withdraw();
        reset();
    }
});

test("a box around the selection holds it, is the active box and selects what it now touches", () => {
    reset();
    const v = boundsViewport(OBJECTS);
    const withdraw = setActiveViewport(v.api);
    try {
        assert.equal(addBoxAroundSelection(), null, "nothing selected: no box");
        v.select("inside");
        const id = addBoxAroundSelection();
        const box = useBBoxSelectionStore.getState().boxes[0];
        assert.equal(box.id, id);
        assert.equal(useBBoxSelectionStore.getState().activeId, id);
        // A little larger than the selection, on every side.
        assert.ok(box.min.every((x) => x < 0.2 && x > 0.1) && box.max.every((x) => x > 0.8 && x < 0.9), JSON.stringify(box));
        // The box around "inside" reaches into "straddling".
        assert.deepEqual(v.selected(), ["inside", "straddling"]);
    } finally {
        withdraw();
        reset();
    }
});

test("the active box's selection follows its bounds and its mode, and only the active box's", () => {
    reset();
    const v = boundsViewport(OBJECTS);
    const withdraw = setActiveViewport(v.api);
    const unfollow = bindActiveBoxSelection();
    try {
        const store = useBBoxSelectionStore.getState();
        const other = store.add({min: [9, 9, 9], max: [12, 12, 12]});
        const id = store.add({min: [0, 0, 0], max: [0.1, 0.1, 0.1]});
        assert.equal(v.calls.length, 0, "adding a box selects nothing by itself");
        // Enlarged, as a dragged handle does: it now reaches the objects.
        store.update(id, {max: [1, 1, 1]});
        assert.deepEqual(v.selected(), ["inside", "straddling"]);
        store.update(id, {mode: "full"});
        assert.deepEqual(v.selected(), ["inside"]);
        // Colour is not what a box selects by, and the other box is not the active one.
        const before = v.calls.length;
        store.update(id, {color: "#ff0000"});
        store.update(other, {max: [13, 13, 13]});
        assert.equal(v.calls.length, before);
    } finally {
        unfollow();
        withdraw();
        reset();
    }
});

test("a rectangle on the screen adds what it takes to the selection, and nothing when it takes nothing", () => {
    reset();
    const v = boundsViewport({a: [-1, -1, -1, 1, 1, 1], b: [20, -1, -1, 22, 1, 1]});
    const withdraw = setActiveViewport(v.api);
    try {
        assert.equal(selectInScreenRect({x0: -1, y0: -1, x1: -0.9, y1: -0.9}, "crossing"), 0);
        assert.equal(v.calls.length, 0, "an empty rectangle leaves the selection and the undo history alone");
        assert.equal(selectInScreenRect(whole, "window"), 1);
        assert.deepEqual(v.calls[0].ids, ["a"]);
        assert.equal(v.calls[0].options.add, true);
        assert.equal(useObjectInfoStore.getState().name, "Window selection: 1 element");
    } finally {
        withdraw();
        reset();
    }
});

test("a set of the scene's own keys reads as object refs, one reused ref for all of them", () => {
    const set = new KeySet([objectKey("model-a", "7"), objectKey("model-b", "x/y")]);
    assert.equal(set.size, 2);
    const seen: ObjectRef[] = [];
    const read: string[] = [];
    for (const ref of set) {
        seen.push(ref);
        read.push(`${ref.model}:${ref.id}`);
    }
    assert.deepEqual(read, ["model-a:7", "model-b:x/y"]);
    assert.equal(seen[0], seen[1], "no ref is made per object");
});
