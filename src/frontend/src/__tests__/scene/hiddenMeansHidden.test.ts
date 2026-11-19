// Hidden means hidden, in every view setting: what a hidden element gets drawn of it in the three.js
// viewport -- the colours painted over it (core's `paintFeaElements`, a plugin's own overlay on the
// FEA mesh), its coloured beam line, its result-point markers, a highlight mark, a label or outline
// a plugin pinned to it -- whether that was drawn before the hide or after it (a View setting
// repainted: colour by, a new step, the result display), and back after unhide; the same for an
// isolate. The three.js-free viewport's side: the paint overlay and the result-point markers are
// objects of the viewport, so its per-segment Hidden flag covers them (asserted on the data).
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import * as THREE from "three";

await import("../fixtures/panelRealm");

import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {addElementOutlines, addLabels, paintFeaElements, removeOwnerOverlays} from "@/plugins/sceneOverlays";
import "@/utils/scene/fea/meshOverlayTarget";
import {installResultLineSegments} from "@/utils/scene/fea/resultLineSegments";
import {installResultPointMarkers, shownMarkers} from "@/utils/scene/fea/resultPointMarkers";
import {pointObjects} from "@/utils/scene/fea/streaming/layerLines";
import {makeLayerOverlayTarget} from "@/utils/scene/fea/streaming/layerOverlayTarget";
import {runBeforeDraw} from "@/viewport/api/beforeDraw";
import {createThreeViewport} from "@/viewport/api/threeViewport";
import {setActiveViewport} from "@/viewport/api/viewportApi";

/** Two quads (elements 1 and 2), split per element as the FEA mesh stores them. */
function feaMesh(): CustomBatchedMesh {
    const quad = (x: number) => [x, 0, 0, x + 1, 0, 0, x + 1, 1, 0, x, 1, 0];
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute([...quad(0), ...quad(1)], 3));
    geom.setIndex([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]);
    const ranges = new Map<string, [number, number]>([["E1", [0, 6]], ["E2", [6, 6]]]);
    return new CustomBatchedMesh(geom, new THREE.MeshBasicMaterial(), ranges, "fea", false, null);
}

let withdraw: (() => void) | null = null;
function open(mesh: THREE.Mesh): THREE.Scene {
    const session = useModelSessionStore.getState().open({sourceName: "m.SIN", kind: "fea"});
    session.fea = {sourceName: "m.SIN", mesh} as unknown as FeaSessionHandle;
    const scene = new THREE.Scene();
    scene.add(mesh);
    withdraw = setActiveViewport(createThreeViewport({scene, camera: new THREE.PerspectiveCamera(), controls: {} as never}));
    return scene;
}
afterEach(() => {
    removeOwnerOverlays("p");
    useModelSessionStore.getState().close();
    withdraw?.();
    withdraw = null;
});

/** The draw ranges of `overlay` (an aligned copy of the mesh's index list) that are drawn. */
function drawnOf(overlay: THREE.Mesh, ranges: Map<string, [number, number]>): string[] {
    const mats = Array.isArray(overlay.material) ? overlay.material : [overlay.material];
    const groups = overlay.geometry.groups.length ? overlay.geometry.groups : [{start: 0, count: Infinity, materialIndex: 0}];
    const out: string[] = [];
    for (const [id, [start]] of ranges) {
        const g = groups.find((x) => start >= x.start && start < x.start + x.count);
        if (g && mats[g.materialIndex ?? 0].visible) out.push(id);
    }
    return out;
}

/** What of element 1 / 2 / beam 3 is drawn by each layer over the mesh. */
function whatIsDrawn(mesh: CustomBatchedMesh): Record<string, string[]> {
    const paint = mesh.children.find((c) => c.name === "p-element-paint") as THREE.Mesh | undefined;
    const plugin = mesh.children.find((c) => c.name === "plugin-color-overlay") as THREE.Mesh | undefined;
    const lines = mesh.getObjectByName("__fea_result_line_segments__");
    const markers = mesh.getObjectByName("__fea_result_point_markers__") as THREE.Points | undefined;
    const markerCount = markers ? markers.geometry.getAttribute("position").count : 0;
    return {
        paint: paint ? drawnOf(paint, mesh.drawRanges) : [],
        plugin: plugin ? drawnOf(plugin, mesh.drawRanges) : [],
        lines: [...new Set((lines?.userData.__feaSegmentIds as string[] | undefined) ?? [])],
        markers: markerCount === 2 ? ["E1", "E2"] : markerCount === 1 ? ["?"] : [],
    };
}

/** A View setting's repaint: the result colours over the mesh drawn anew -- core's paint, a plugin's
 *  overlay, the beam lines and the markers. */
function repaint(mesh: CustomBatchedMesh): void {
    removeOwnerOverlays("p");
    paintFeaElements("p", {colors: new Map([[1, "#ff0000"], [2, "#00ff00"]])});
    mesh.children.filter((c) => c.name === "plugin-color-overlay").forEach((c) => c.removeFromParent());
    // A plugin's colour overlay as it builds one: the mesh's triangles de-indexed, hung on the mesh.
    const n = mesh.geometry.index!.count;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const overlay = new THREE.Mesh(g, new THREE.MeshBasicMaterial({vertexColors: true}));
    overlay.name = "plugin-color-overlay";
    mesh.add(overlay);
    const seg = new Float32Array([0, 0, 0, 0, 0, 1]);
    installResultLineSegments(mesh, seg, seg, new Float32Array(6), ["E3", "E3"]);
    installResultPointMarkers(mesh, new Float32Array(6), new Float32Array(6), new Float32Array(6), [1, 2]);
}

const ACTIONS = {
    hide: (m: CustomBatchedMesh) => m.hideBatchDrawRange(["E1", "E3"]),
    isolate: (m: CustomBatchedMesh) => m.isolateDrawRanges(new Set(["E2"])),
} as const;

for (const [action, run] of Object.entries(ACTIONS)) {
    for (const when of ["painted before", "repainted after (a View setting changed)"] as const) {
        test(`${action}, ${when}: a hidden element has no colour, line, marker or mark; unhide brings them back`, () => {
            const mesh = feaMesh();
            open(mesh);
            if (when === "painted before") repaint(mesh);
            mesh.setMarks(new Map([["E1", 0xff0000], ["E2", 0x00ff00]]));
            run(mesh);
            if (when !== "painted before") repaint(mesh);
            const drawn = whatIsDrawn(mesh);
            assert.deepEqual(drawn.paint, ["E2"], "core's paint");
            assert.deepEqual(drawn.plugin, ["E2"], "a plugin's overlay on the FEA mesh");
            assert.deepEqual(drawn.markers, ["?"], "one marker left, E2's");
            if (action === "hide") assert.deepEqual(drawn.lines, [], "the hidden beam's line");
            const marks = mesh.children.find((c) => mesh.isHighlightOverlay(c) && (c as THREE.Mesh).renderOrder === 9) as THREE.Mesh | undefined;
            assert.deepEqual(marks?.geometry.groups.map((x) => x.start), [6], "only E2's mark");

            mesh.unhideAllDrawRanges();
            const back = whatIsDrawn(mesh);
            assert.deepEqual(back.paint, ["E1", "E2"]);
            assert.deepEqual(back.plugin, ["E1", "E2"]);
            assert.deepEqual(back.markers, ["E1", "E2"]);
            assert.deepEqual(back.lines, ["E3"]);
            assert.deepEqual(marks?.geometry.groups.map((x) => x.start).sort(), [0, 6]);
        });
    }
}

test("a label pinned to hidden elements goes, and comes back on unhide; an outline leaves hidden elements out", () => {
    const mesh = feaMesh();
    open(mesh);
    // A viewport that only counts what it is asked to draw.
    const labels = new Map<number, unknown>();
    const lines = new Map<number, Float32Array>();
    let next = 1;
    withdraw?.();
    withdraw = setActiveViewport({
        overlays: () => ({
            addLines: (spec: {segments: Float32Array}) => (lines.set(next, spec.segments), next++),
            remove: (h: number) => void lines.delete(h),
            setPositions: (h: number, p: Float32Array) => void lines.set(h, p),
            dispose: () => {},
        }),
        billboards: () => ({add: (b: unknown) => (labels.set(next, b), next++), remove: (h: number) => void labels.delete(h), update: () => {}, dispose: () => {}}),
    } as never);
    addLabels("p", [{text: "UF 0.9", atElements: [1]}, {text: "UF 0.4", atElements: [2]}]);
    addElementOutlines("p", {elements: [1, 2]});
    const outline = () => [...lines.values()].reduce((n, seg) => n + seg.length / 6, 0);
    assert.equal(labels.size, 2);
    const both = outline();
    assert.equal(both, 6, "around the pair");
    mesh.hideBatchDrawRange(["E1"]);
    runBeforeDraw();
    assert.equal(labels.size, 1, "the hidden element's label is taken away");
    assert.equal(outline(), 4, "the outline is the shown element's only");
    mesh.unhideAllDrawRanges();
    runBeforeDraw();
    assert.equal(labels.size, 2);
    assert.equal(outline(), both);
    // Pinned after the hide: drawn without the hidden one from the start.
    mesh.hideBatchDrawRange(["E2"]);
    removeOwnerOverlays("p");
    addLabels("p", [{text: "UF 0.4", atElements: [2]}]);
    assert.equal(labels.size, 0);
});

test("the markers a hidden element gets: none (the pure filter both viewports' data goes through)", () => {
    const data = {positions: new Float32Array([0, 0, 0, 1, 1, 1]), colors: new Float32Array(6).fill(1), displacement: new Float32Array(6), labels: [1, 2]};
    assert.equal(shownMarkers(data, new Set(["E1"])).positions.length, 3);
    assert.deepEqual(shownMarkers(data, new Set(["E1"])).labels, [2]);
    assert.equal(shownMarkers(data, new Set()), data, "nothing hidden: as painted");
    assert.equal(shownMarkers({...data, labels: undefined}, new Set(["E1"])).positions.length, 6, "points naming no element stay");
});

test("three.js-free viewport: the paint overlay and the markers are objects, so its Hidden flag covers them", () => {
    // The markers: one segment per point, each its element's object.
    const objects = pointObjects({model: "m", ids: ["E1", "E2"], segments: new Uint32Array(), objectOf: new Int32Array()}, [2, 1, 2], (l) => l - 1);
    assert.deepEqual([...objects.segments], [0, 1, 1, 1, 2, 1]);
    assert.deepEqual([...objects.objectOf], [1, 0, 1]);
    // The paint: each painted element's corners one segment of the overlay layer.
    const made: {data?: {objects?: {segments: Uint32Array; objectOf: Int32Array; ids: readonly string[]}}} = {};
    const layer = {setColors: () => {}};
    const target = makeLayerOverlayTarget({
        results: {createResultMesh: (d: never) => ((made.data = d), layer)} as never,
        matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        model: "m",
        ids: ["E1", "E2"],
        mesh: () => ({positions: new Float32Array(24), indices: Uint32Array.from([0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]), ranges: new Map([["E1", [0, 6]], ["E2", [6, 6]]]), displacement: () => null}),
        scale: () => 0,
        serial: () => 0,
        pin: () => () => {},
    });
    target.paint({on: "model", name: "p", colorOf: (label, out) => (out.set([1, 0, 0]), label === 2 || label === 1)});
    assert.ok(made.data);
    assert.deepEqual([...made.data.objects!.segments], [0, 6, 6, 6]);
    assert.deepEqual([...made.data.objects!.objectOf], [0, 1]);
});
