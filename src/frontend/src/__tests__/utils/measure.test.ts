// The Measure tab: its arithmetic, the snapping to corners / edge midpoints / edges / the surface, the
// coordinates of a recentred model, the store's measurements, the tool holding and giving back the
// scene's clicks, the drawing, and reading an object's triangles out of a GLB (the three.js-free
// viewport's way to its feature edges).
import assert from "node:assert/strict";
import {test} from "node:test";

import {
    angleAt,
    angleBetweenLines,
    arcSegments,
    components,
    distance,
    formatAngle,
    formatLength,
    formatPoint,
    pathLength,
    type Vec3,
} from "@/utils/measure/measureMath";
import {featureGraph, snapPoint, type Projector} from "@/utils/measure/snap";
import {
    addMeasurePoint,
    clearMeasurements,
    endPendingMeasurement,
    setMeasureTool,
    useMeasureStore,
    type MeasurePoint,
} from "@/state/measureStore";
import {measurementsText, summarise} from "@/utils/measure/measurements";
import {armMeasureTool, disarmMeasureTool, measurePoint, snapAt} from "@/utils/measure/measureController";
import {drawMeasurement} from "@/utils/measure/measureDrawing";
import {offerSceneClick, sceneClicksHeld, takeSceneClicks} from "@/viewport/api/sceneClicks";
import {setActiveViewport, type ObjectRef, type SurfaceHit, type ViewportApi} from "@/viewport/api/viewportApi";
import type {OverlayLayer, OverlayLines, OverlayPoints} from "@/viewport/api/overlayLayer";
import type {Billboard, BillboardLayer} from "@/viewport/api/billboards";
import {setSceneOffset} from "@/state/sceneFrameStore";
import {computeEdgeArrays} from "@/utils/mesh_select/edgeCore";
import {glbObjectReader} from "@/utils/glb/glbObjectTriangles";

const close = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);
const closeVec = (a: readonly number[], b: readonly number[], eps = 1e-6) => a.forEach((v, i) => close(v, b[i], eps));

// ----- arithmetic ------------------------------------------------------------------------------------

test("distance and its components", () => {
    close(distance([0, 0, 0], [3, 4, 12]), 13);
    const c = components([1, 2, 3], [4, -2, 3]);
    close(c.length, 5);
    assert.deepEqual([c.dx, c.dy, c.dz], [3, -4, 0]);
});

test("the angle at a vertex, and between two lines", () => {
    close(angleAt([1, 0, 0], [0, 0, 0], [0, 1, 0])!, 90);
    close(angleAt([1, 0, 0], [0, 0, 0], [-1, 0, 0])!, 180);
    close(angleAt([2, 2, 0], [1, 1, 0], [3, 1, 0])!, 45);
    assert.equal(angleAt([0, 0, 0], [0, 0, 0], [1, 0, 0]), null, "an arm of no length");
    // Lines have no direction: 150 between the vectors is 30 between the lines.
    const u: Vec3 = [1, 0, 0];
    const v: Vec3 = [-Math.cos(Math.PI / 6), Math.sin(Math.PI / 6), 0];
    close(angleBetweenLines(u, v)!, 30);
    close(angleBetweenLines([0, 0, 1], [0, 0, -5])!, 0);
});

test("a path's legs and total", () => {
    const {segments, total} = pathLength([[0, 0, 0], [3, 0, 0], [3, 4, 0], [3, 4, 2]]);
    assert.deepEqual(segments, [3, 4, 2]);
    close(total, 9);
    assert.deepEqual(pathLength([[1, 1, 1]]), {segments: [], total: 0});
});

test("the arc of an angle runs from arm to arm at its radius", () => {
    const arc = arcSegments([2, 0, 0], [0, 0, 0], [0, 3, 0], 0.5, 8);
    assert.equal(arc.length, 8 * 6);
    closeVec([arc[0], arc[1], arc[2]], [0.5, 0, 0]);
    closeVec([arc[arc.length - 3], arc[arc.length - 2], arc[arc.length - 1]], [0, 0.5, 0]);
    for (let i = 0; i < arc.length; i += 3) close(Math.hypot(arc[i], arc[i + 1], arc[i + 2]), 0.5, 1e-6);
});

test("values are written in metres and degrees", () => {
    assert.equal(formatLength(1.23456), "1.235 m");
    assert.equal(formatLength(0.012345), "0.0123 m");
    assert.equal(formatLength(-0.00001), "0.0000 m", "no minus zero");
    assert.equal(formatAngle(45), "45.00°");
    assert.equal(formatPoint([1, -2.5, 0.0004]), "1.000, -2.500, 0.000");
});

// ----- snapping --------------------------------------------------------------------------------------

/** Looking down -z: 100 pixels per unit, y up on the world is up on the screen. */
const project: Projector = (p) => [p[0] * 100, -p[1] * 100];

/** A unit square in z = 0, its bottom edge in two pieces (as a triangulation splits an edge). */
const SQUARE = Float32Array.from([
    0, 0, 0, 0.3, 0, 0,
    0.3, 0, 0, 1, 0, 0,
    1, 0, 0, 1, 1, 0,
    1, 1, 0, 0, 1, 0,
    0, 1, 0, 0, 0, 0,
]);

test("the corners of feature edges are where they turn, not every end of a segment", () => {
    const g = featureGraph(SQUARE);
    const corners = [];
    for (let i = 0; i < g.corners.length; i += 3) corners.push([g.corners[i], g.corners[i + 1], g.corners[i + 2]]);
    assert.equal(corners.length, 4, "the split in the bottom edge is no corner");
    assert.ok(!corners.some((c) => Math.abs(c[0] - 0.3) < 1e-6 && c[1] === 0));
    // Four edges, each with its middle by length: the bottom one's is at 0.5, not at the split.
    assert.equal(g.midpoints.length, 4);
    assert.ok(g.midpoints.some((m) => Math.abs(m.point[0] - 0.5) < 1e-6 && Math.abs(m.point[1]) < 1e-6));
    // A circle drawn as 16 segments turns 22.5 degrees at each end: no corners, no middle.
    const circle: number[] = [];
    for (let i = 0; i < 16; i++) {
        const a = (i / 16) * Math.PI * 2, b = ((i + 1) / 16) * Math.PI * 2;
        circle.push(Math.cos(a), Math.sin(a), 0, Math.cos(b), Math.sin(b), 0);
    }
    const round = featureGraph(Float32Array.from(circle));
    assert.equal(round.corners.length, 0);
    assert.equal(round.midpoints.length, 0);
});

test("a click snaps to the nearest corner within the tolerance, else an edge's middle, else the edge, else the surface", () => {
    const g = [featureGraph(SQUARE)];
    // Five pixels off the top-right corner: the corner.
    const corner = snapPoint([104, -97], [0.98, 0.95, 0], g, project);
    assert.equal(corner?.kind, "corner");
    closeVec(corner!.world, [1, 1, 0]);
    // Near the middle of the bottom edge: its middle, though the pointer is at 0.53.
    const middle = snapPoint([53, 3], [0.53, 0.03, 0], g, project);
    assert.equal(middle?.kind, "midpoint");
    closeVec(middle!.world, [0.5, 0, 0]);
    // Along the edge, away from corners and the middle: the point of the edge under the pointer.
    const edge = snapPoint([80, 4], [0.8, 0.04, 0], g, project);
    assert.equal(edge?.kind, "edge");
    closeVec(edge!.world, [0.8, 0, 0], 1e-4);
    assert.deepEqual(edge!.edge!.map((p) => [...p]), [[0.3, 0, 0], [1, 0, 0]].map((p) => p.map((v) => Math.fround(v))));
    // In the middle of the face: the surface point.
    const surface = snapPoint([50, -50], [0.5, 0.5, 0], g, project);
    assert.deepEqual(surface, {kind: "surface", world: [0.5, 0.5, 0]});
    // Nothing under the pointer and nothing near it.
    assert.equal(snapPoint([500, 500], null, g, project), null);
    // Just off the silhouette, with no surface: the edge still catches it.
    assert.equal(snapPoint([70, 5], null, g, project)?.kind, "edge");
    // Snapping off: the surface as it is.
    assert.equal(snapPoint([104, -97], [0.98, 0.95, 0], g, project, {snap: false})?.kind, "surface");
    // Tolerances in pixels: 13 px from the corner is too far for a corner at 12.
    assert.notEqual(snapPoint([113, -100], [1.13, 1, 0], g, project)?.kind, "corner");
});

test("a corner behind the surface under the pointer is not snapped to", () => {
    // A second square 5 below the first, seen through it from above.
    const below = SQUARE.map((v, i) => (i % 3 === 2 ? -5 : v));
    const front = [featureGraph(SQUARE.map((v, i) => (i % 3 === 0 ? v + 3 : v)))]; // elsewhere on the screen
    const g = [...front, featureGraph(below)];
    const eye: Vec3 = [0.5, 0.5, 10];
    // The surface under the pointer is at z = 0; the far square's corner projects right there.
    const hit = snapPoint([2, -2], [0.02, 0.02, 0], g, project, {eye});
    assert.equal(hit?.kind, "surface");
    // Without a surface in front (the pointer is off the model), it is taken.
    assert.equal(snapPoint([2, -2], null, g, project, {eye})?.kind, "corner");
});

// ----- the store -------------------------------------------------------------------------------------

const pt = (x: number, y: number, z: number, edge?: readonly [Vec3, Vec3]): MeasurePoint => ({world: [x, y, z], model: [x, y, z], kind: edge ? "edge" : "surface", ...(edge ? {edge} : {})});

test("each tool finishes on its number of points; a path when it is ended", () => {
    clearMeasurements();
    setMeasureTool("distance");
    assert.equal(addMeasurePoint(pt(0, 0, 0)), "added");
    assert.equal(addMeasurePoint(pt(3, 4, 0)), "done");
    const [d] = useMeasureStore.getState().measurements;
    assert.equal(summarise(d).value, "5.000 m");
    assert.deepEqual(summarise(d).details, ["dX +3.000 m", "dY +4.000 m", "dZ 0.0000 m"]);

    setMeasureTool("angle");
    addMeasurePoint(pt(1, 0, 0));
    addMeasurePoint(pt(0, 0, 0));
    assert.equal(addMeasurePoint(pt(0, 2, 0)), "done");
    assert.equal(summarise(useMeasureStore.getState().measurements[1]).value, "90.00°");

    // Edges have to be clicked for an edge angle.
    setMeasureTool("edgeAngle");
    assert.equal(addMeasurePoint(pt(0, 0, 0)), "refused");
    assert.match(useMeasureStore.getState().notice ?? "", /edge/);
    addMeasurePoint(pt(0.5, 0, 0, [[0, 0, 0], [1, 0, 0]]));
    assert.equal(addMeasurePoint(pt(0, 0.5, 0, [[0, 0, 0], [1, 1, 0]])), "done");
    assert.equal(summarise(useMeasureStore.getState().measurements[2]).value, "45.00°");

    setMeasureTool("path");
    addMeasurePoint(pt(0, 0, 0));
    addMeasurePoint(pt(1, 0, 0));
    assert.equal(addMeasurePoint(pt(1, 2, 0)), "added", "a path goes on until it is ended");
    assert.equal(endPendingMeasurement(), true);
    const path = useMeasureStore.getState().measurements[3];
    assert.equal(summarise(path).value, "3.000 m");
    assert.deepEqual(summarise(path).details, ["leg 1: 1.000 m", "leg 2: 2.000 m"]);

    // Ending a distance half made drops it.
    setMeasureTool("distance");
    addMeasurePoint(pt(0, 0, 0));
    assert.equal(endPendingMeasurement(), true);
    assert.equal(endPendingMeasurement(), false, "nothing left to end");
    assert.equal(useMeasureStore.getState().measurements.length, 4);

    setMeasureTool("point");
    assert.equal(addMeasurePoint({world: [11, 22, 33], model: [1, 2, 3], kind: "corner"}), "done");
    const point = useMeasureStore.getState().measurements[4];
    assert.equal(summarise(point).value, "1.000, 2.000, 3.000");
    assert.deepEqual(summarise(point).details, ["model: 1.000, 2.000, 3.000", "viewer: 11.000, 22.000, 33.000"]);

    const text = measurementsText(useMeasureStore.getState().measurements);
    assert.match(text, /^Distance 1: 5\.000 m\n  dX \+3\.000 m/);
    assert.match(text, /Point 5: 1\.000, 2\.000, 3\.000/);

    clearMeasurements();
    setMeasureTool(null);
    assert.deepEqual(useMeasureStore.getState().measurements, []);
});

// ----- a viewport to measure in ----------------------------------------------------------------------

function fakeLayers() {
    const drawn = {lines: [] as OverlayLines[], points: [] as OverlayPoints[], labels: [] as Billboard[]};
    const lines: OverlayLayer = {
        addLines: (l) => drawn.lines.push(l),
        addPoints: (p) => drawn.points.push(p),
        addMesh: () => 0,
        setPositions: () => {},
        setGeometry: () => {},
        setLook: () => {},
        remove: () => {},
        clear: () => {
            drawn.lines = [];
            drawn.points = [];
        },
        dispose: () => {},
    };
    const labels: BillboardLayer = {
        add: (b) => drawn.labels.push(b),
        update: () => {},
        remove: () => {},
        clear: () => void (drawn.labels = []),
        onClick: () => () => {},
        dispose: () => {},
    };
    return {drawn, lines, labels};
}

/** A viewport over a scene that holds one unit square, recentred by `offset`: the square is at
 *  model z = 0, drawn at world = model + offset. The camera looks down -z. */
function fakeViewport(offset: Vec3, surface: (x: number, y: number) => SurfaceHit | null) {
    const ref: ObjectRef = {model: "m", id: "plate"};
    const world = SQUARE.map((v, i) => v + offset[i % 3]);
    const toScreen = (p: ArrayLike<number>): [number, number] => [(p[0] - offset[0]) * 100, -(p[1] - offset[1]) * 100];
    const layers = fakeLayers();
    let selections = 0;
    const viewport = {
        kind: "native",
        surfaceAt: async (x: number, y: number) => surface(x, y),
        featureEdges: (r: ObjectRef) => (r.id === ref.id ? world : null),
        projectToScreen: (w: Float32Array, count: number, out: Float32Array) => {
            for (let i = 0; i < count; i++) {
                const [sx, sy] = toScreen(w.subarray(i * 3, i * 3 + 3));
                out[i * 3] = sx;
                out[i * 3 + 1] = sy;
                out[i * 3 + 2] = 0.5;
            }
            return {width: 1000, height: 1000};
        },
        cameraPose: () => ({eye: [offset[0] + 0.5, offset[1] + 0.5, offset[2] + 10], target: [...offset], up: [0, 1, 0], fovDeg: 45}),
        overlays: () => layers.lines,
        billboards: () => layers.labels,
        setSelection: () => (selections++, 0),
        clearSelection: () => void selections++,
        onCameraChange: () => () => {},
    } as unknown as ViewportApi;
    return {viewport, ref, layers, selections: () => selections};
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("a recentred model: the snapped corner is drawn in the world and reported in the model's coordinates", async () => {
    const offset: Vec3 = [-1000, 250, 5];
    setSceneOffset(offset);
    const {viewport, ref} = fakeViewport(offset, (x, y) => ({ref, world: [x / 100 + offset[0], -y / 100 + offset[1], offset[2]]}));
    // Three pixels off the model's corner (1, 0, 0).
    const snapped = await snapAt(viewport, 103, 2);
    assert.equal(snapped?.kind, "corner");
    closeVec(snapped!.world, [1 + offset[0], offset[1], offset[2]], 1e-3);
    const point = measurePoint(snapped!);
    closeVec(point.model, [1, 0, 0], 1e-3);
    closeVec(point.world, [-999, 250, 5], 1e-3);
    setSceneOffset(null);
    // With no offset, the model's coordinates are the world's.
    closeVec(measurePoint({kind: "surface", world: [4, 5, 6]}).model, [4, 5, 6]);
});

test("a tool holds the scene's clicks, measures with them and gives them back", async () => {
    clearMeasurements();
    const offset: Vec3 = [10, 20, 30];
    setSceneOffset(offset);
    const {viewport, ref, selections} = fakeViewport(offset, (x, y) => ({ref, world: [x / 100 + offset[0], -y / 100 + offset[1], offset[2]]}));
    const withdraw = setActiveViewport(viewport);
    try {
        assert.equal(sceneClicksHeld(), false);
        armMeasureTool("distance");
        assert.equal(sceneClicksHeld(), true);
        assert.equal(useMeasureStore.getState().tool, "distance");
        // Two clicks, near two corners of the square: taken, so the viewport selects nothing.
        assert.equal(offerSceneClick({x: 2, y: -3, additive: false}), true);
        await tick();
        assert.equal(offerSceneClick({x: 98, y: -101, additive: true}), true);
        await tick();
        const [m] = useMeasureStore.getState().measurements;
        assert.ok(m, "a distance was measured");
        closeVec(m.points[0].model, [0, 0, 0], 1e-4);
        closeVec(m.points[1].model, [1, 1, 0], 1e-4);
        assert.equal(summarise(m).value, "1.414 m");
        assert.equal(selections(), 0, "the selection was not touched");
        // Switching the tool off gives the clicks back.
        disarmMeasureTool();
        assert.equal(sceneClicksHeld(), false);
        assert.equal(offerSceneClick({x: 2, y: -3, additive: false}), false);
        assert.equal(useMeasureStore.getState().tool, null);
        assert.equal(useMeasureStore.getState().measurements.length, 1, "what was measured stays");
    } finally {
        disarmMeasureTool();
        withdraw();
        setSceneOffset(null);
        clearMeasurements();
    }
});

test("clicks a tool holds come before the viewport, newest holder first", () => {
    const seen: string[] = [];
    const a = takeSceneClicks("a", () => void seen.push("a"));
    const b = takeSceneClicks("b", () => (seen.push("b"), false));
    assert.equal(offerSceneClick({x: 0, y: 0, additive: false}), true);
    assert.deepEqual(seen, ["b", "a"]);
    a.end();
    b.end();
    assert.equal(offerSceneClick({x: 0, y: 0, additive: false}), false);
});

// ----- drawing ---------------------------------------------------------------------------------------

test("a distance is drawn as its points, its line, its three components and a label", () => {
    const {drawn, lines, labels} = fakeLayers();
    drawMeasurement(lines, labels, {tool: "distance", points: [pt(0, 0, 0), pt(1, 2, 3)]});
    assert.equal(drawn.points.length, 1);
    assert.equal(drawn.lines.length, 4);
    assert.ok(drawn.lines.every((l) => l.onTop), "over the model");
    assert.equal(drawn.labels.length, 1);
    closeVec(drawn.labels[0].position, [0.5, 1, 1.5]);
    // An angle: its arms, its arc, its label at the arc.
    const angle = fakeLayers();
    drawMeasurement(angle.lines, angle.labels, {tool: "angle", points: [pt(1, 0, 0), pt(0, 0, 0), pt(0, 1, 0)]});
    assert.equal(angle.drawn.lines.length, 2);
    assert.equal(angle.drawn.labels.length, 1);
    // One being made has no label yet.
    const pending = fakeLayers();
    drawMeasurement(pending.lines, pending.labels, {tool: "distance", points: [pt(0, 0, 0), pt(1, 0, 0)]}, true);
    assert.equal(pending.drawn.labels.length, 0);
});

// ----- an object's triangles out of a GLB ------------------------------------------------------------

/** A GLB of one node (translated by `t`) drawing two objects of a merged mesh: a unit quad (two
 *  triangles) for "plate" and one triangle for "tri", named by adapy's draw ranges. */
function tinyGlb(t: Vec3): Uint8Array {
    const positions = new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 5, 5, 5, 6, 5, 5, 5, 6, 5]);
    const indices = new Uint32Array([0, 1, 2, 0, 2, 3, 4, 5, 6]);
    const bin = new Uint8Array(positions.byteLength + indices.byteLength);
    bin.set(new Uint8Array(positions.buffer), 0);
    bin.set(new Uint8Array(indices.buffer), positions.byteLength);
    const json = {
        asset: {version: "2.0"},
        scene: 0,
        scenes: [{nodes: [0], extras: {draw_ranges_merged: {plate: [0, 6], tri: [6, 3]}}}],
        nodes: [{name: "merged", mesh: 0, translation: [...t]}],
        meshes: [{primitives: [{attributes: {POSITION: 0}, indices: 1, mode: 4}]}],
        accessors: [
            {bufferView: 0, componentType: 5126, count: 7, type: "VEC3"},
            {bufferView: 1, componentType: 5125, count: 9, type: "SCALAR"},
        ],
        bufferViews: [
            {buffer: 0, byteOffset: 0, byteLength: positions.byteLength},
            {buffer: 0, byteOffset: positions.byteLength, byteLength: indices.byteLength},
        ],
        buffers: [{byteLength: bin.byteLength}],
    };
    let text = JSON.stringify(json);
    while (text.length % 4) text += " ";
    const jsonBytes = new TextEncoder().encode(text);
    const total = 12 + 8 + jsonBytes.length + 8 + bin.length;
    const out = new Uint8Array(total);
    const view = new DataView(out.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, total, true);
    view.setUint32(12, jsonBytes.length, true);
    view.setUint32(16, 0x4e4f534a, true);
    out.set(jsonBytes, 20);
    const at = 20 + jsonBytes.length;
    view.setUint32(at, bin.length, true);
    view.setUint32(at + 4, 0x004e4942, true);
    out.set(bin, at + 8);
    return out;
}

test("an object's triangles are read out of the GLB, placed by its node and the scene's offset", () => {
    const reader = glbObjectReader(tinyGlb([100, 0, 0]));
    const plate = reader.triangles("plate", [-100, 1, 0])!;
    assert.equal(plate.indices.length, 6);
    assert.equal(plate.positions.length, 4 * 3, "only the vertices it uses");
    // Node translation (+100, 0, 0) then the offset (-100, +1, 0).
    closeVec(Array.from(plate.positions.subarray(0, 3)), [0, 1, 0]);
    closeVec(Array.from(plate.positions.subarray(6, 9)), [1, 2, 0]);
    const tri = reader.triangles("tri")!;
    closeVec(Array.from(tri.positions.subarray(0, 3)), [105, 5, 5]);
    assert.equal(reader.triangles("nothing"), null);
    // Its feature edges (the outline rule) make a square: four corners, not the diagonal.
    const edges = computeEdgeArrays(plate.positions, plate.indices, [[0, plate.indices.length]], Math.cos(Math.PI / 6)).positions;
    assert.equal(edges.length / 6, 4, "the shared diagonal is no feature edge");
    assert.equal(featureGraph(edges).corners.length / 3, 4);
});
