// A streamed result on a viewport that draws from typed arrays: the mesh is read from the bake's
// GLB, a nodal field is painted through the viewport's result layers, the controls and the legend
// are registered as for any result, and stepping and playing go through the same store.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

// The scope store persists to sessionStorage, which node has not got.
const memory = new Map<string, string>();
(globalThis as unknown as {sessionStorage: unknown}).sessionStorage = {
    getItem: (k: string): string | null => memory.get(k) ?? null,
    setItem: (k: string, v: string): void => void memory.set(k, String(v)),
    removeItem: (k: string): void => void memory.delete(k),
};

const {useColorStore} = await import("@/state/colorLegendStore");
const {useFeaAnimationStore} = await import("@/state/feaAnimationStore");
const {getColormap} = await import("@/utils/scene/fea/colormaps");
const {stepFeaSweep} = await import("@/utils/scene/fea/feaAnimationDriver");
const {setResultStep, stepResults} = await import("@/utils/scene/fea/playbackActions");
const {closeLayerSession, layerSessionActive, loadFeaOnLayers} = await import("@/utils/scene/fea/streaming/layerSession");
const {onModelLoaded, setActiveViewport} = await import("@/viewport/api/viewportApi");
const {useVisibilityStore} = await import("@/state/visibilityStore");
const overlays = await import("@/plugins/sceneOverlays");
const {runBeforeDraw} = await import("@/viewport/api/beforeDraw");
import type {Billboard} from "@/viewport/api/billboards";
import type {OverlayLines} from "@/viewport/api/overlayLayer";
import type {FeaCapability} from "@/services/capabilities/types";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import type {ResultMeshData, ResultMeshLayer} from "@/viewport/api/resultLayer";
import type {ViewportApi} from "@/viewport/api/viewportApi";

/** Four nodes, two triangles: an uncompressed GLB as the bake writes it. */
function meshGlb(): ArrayBuffer {
    const positions = new Float32Array([0, 0, 0, 3, 0, 0, 3, 4, 0, 0, 4, 0]);
    const indices = new Uint32Array([0, 1, 2, 0, 2, 3]);
    const bin = new Uint8Array(positions.byteLength + indices.byteLength);
    bin.set(new Uint8Array(positions.buffer), 0);
    bin.set(new Uint8Array(indices.buffer), positions.byteLength);
    const json = new TextEncoder().encode(JSON.stringify({
        asset: {version: "2.0"},
        scenes: [{nodes: [0]}],
        nodes: [{mesh: 0}],
        meshes: [{primitives: [{attributes: {POSITION: 0}, indices: 1}]}],
        accessors: [
            {bufferView: 0, componentType: 5126, count: 4, type: "VEC3"},
            {bufferView: 1, componentType: 5125, count: 6, type: "SCALAR"},
        ],
        bufferViews: [{buffer: 0, byteOffset: 0, byteLength: 48}, {buffer: 0, byteOffset: 48, byteLength: 24}],
        buffers: [{byteLength: 72}],
    }));
    const jsonLength = Math.ceil(json.length / 4) * 4;
    const out = new Uint8Array(12 + 8 + jsonLength + 8 + bin.length).fill(0x20);
    const view = new DataView(out.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, out.length, true);
    view.setUint32(12, jsonLength, true);
    view.setUint32(16, 0x4e4f534a, true);
    out.set(json, 20);
    view.setUint32(20 + jsonLength, bin.length, true);
    view.setUint32(24 + jsonLength, 0x004e4942, true);
    out.set(bin, 28 + jsonLength);
    return out.buffer;
}

/** Two steps of a displacement: node 2 moves 1 in z, then 2; the others stay. */
const STEPS = [
    new Float32Array([0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0]),
    new Float32Array([0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0.5]),
];
const HEADER = 64;

function displacement(over: Partial<FeaManifestField> = {}): FeaManifestField {
    return {
        name_canonical: "displacement",
        name_native: "U",
        kind: "vector3",
        category: "displacement",
        support: "nodal",
        analysis_kind: "eigen",
        components: ["X", "Y", "Z"],
        n_steps: 2,
        steps: [{i: 0, value: 1, label: "1"}, {i: 1, value: 2, label: "2"}],
        scalar_range: {magnitude: [0, 2]},
        default_view: {reduction: "magnitude", colormap: "viridis"},
        blob: {url: "fea.displacement.bin", header_bytes: HEADER, stride_bytes: 48, dtype: "float32", byte_order: "little"},
        ...over,
    } as FeaManifestField;
}

const manifest = (fields: FeaManifestField[], mesh: Record<string, unknown> = {}): FeaManifest =>
    ({
        version: 2, src: "deck.frd", fields,
        mesh: {url: "fea.mesh.glb", n_points: 4, n_cells: 2, elements_url: "fea.mesh.elements.bin", ...mesh},
    }) as unknown as FeaManifest;

/** The elements sidecar: element 7 is the first triangle, element 8 the second. */
function elementsSidecar(): ArrayBuffer {
    const out = new Uint32Array([0x4d454641, 1, 2, 0, 7, 0, 1, 8, 1, 1]);
    return out.buffer;
}

/** The element edges sidecar: the outline of the two triangles, as pairs of nodes. */
function edgesSidecar(): ArrayBuffer {
    const pairs = [0, 1, 1, 2, 2, 3, 3, 0];
    const out = new Uint32Array(4 + pairs.length);
    out.set([0x47454641, 1, pairs.length / 2, 0]);
    out.set(pairs, 4);
    return out.buffer;
}

/** One value per element: element 7 carries 10, element 8 carries 30. */
const ELEMENT_VALUES = new Float32Array([10, 30]);

function stress(over: Partial<FeaManifestField> = {}): FeaManifestField {
    return {
        name_canonical: "stress",
        name_native: "S",
        kind: "scalar",
        category: "stress",
        support: "element_average",
        analysis_kind: "static",
        components: ["S"],
        n_steps: 1,
        steps: [{i: 0, value: 1, label: "1"}],
        scalar_range: {S: [0, 100]},
        default_view: {reduction: "S", colormap: "viridis"},
        per_type: [{
            elem_type: "triangle", n_elements: 2, n_ips: 1, ip_layout: [], element_labels: [7, 8],
            element_node_indices: [[0, 1, 2], [0, 2, 3]],
            blob: {url: "fea.stress.triangle.bin", header_bytes: 1024, stride_bytes: 8, dtype: "float32", byte_order: "little"},
            scalar_range: {S: [0, 100]},
        }],
        ...over,
    } as unknown as FeaManifestField;
}

const fea: FeaCapability = {
    supports: () => true,
    fetchManifest: async () => {
        throw new Error("not asked for in this test");
    },
    fieldFetchers: () => ({
        cacheKey: `test-${Math.random()}`,
        fetcher: async (url: string) => {
            if (url === "fea.mesh.glb") return meshGlb();
            if (url === "fea.mesh.elements.bin") return elementsSidecar();
            if (url === "fea.mesh.edges.bin") return edgesSidecar();
            if (url === "fea.mesh.elements.beam.bin") return new Uint32Array([0x4d454641, 1, 3, 0, 7, 0, 1, 8, 1, 1, 20, 2, 0]).buffer;
            if (url === "fea.beam_solids.compact.bin") return new ArrayBuffer(8);
            throw new Error(`unexpected fetch of ${url}`);
        },
        rangeFetcher: async (url: string, start: number) => {
            if (url === "fea.stress.triangle.bin") return {buf: ELEMENT_VALUES.slice().buffer, ranged: true};
            if (url === "fea.stress.line.bin") return {buf: new Float32Array([20]).buffer, ranged: true};
            const step = STEPS[(start - HEADER) / 48];
            return {buf: step.slice().buffer, ranged: true};
        },
    }),
} as unknown as FeaCapability;

interface Recorded {
    data: ResultMeshData;
    replaceModel: boolean;
    colors: number[];
    displacement: number[];
    influence: number;
    colorsVisible: boolean;
    visible: boolean;
    removed: boolean;
}

/** Overlay lines as a viewport was asked to draw them, and where they were last moved to. */
interface RecordedLines {
    spec: OverlayLines;
    segments: number[];
    removed: boolean;
}

/** A viewport that records what it is asked to draw. */
function fakeViewport(): {
    layers: Recorded[]; hidden: Set<string>; withdraw: () => void;
    lines: RecordedLines[]; labels: Map<number, Billboard>; calls: string[];
} {
    const layers: Recorded[] = [];
    const hidden = new Set<string>();
    const lines: RecordedLines[] = [];
    const labels = new Map<number, Billboard>();
    const calls: string[] = [];
    const ids = (refs: Iterable<{model: string; id: string}>) => Array.from(refs, (r) => r.id).join(",");
    const api = {
        kind: "native",
        objectVisibility: () => (_model: string, id: string) => (hidden.has(id) ? "hidden" : "shown"),
        isolate: (refs: Iterable<{model: string; id: string}>) => void calls.push(`isolate(${ids(refs)})`),
        hide: (refs: Iterable<{model: string; id: string}>) => void calls.push(`hide(${ids(refs)})`),
        showAll: () => void calls.push("showAll"),
        show: (refs: Iterable<{model: string; id: string}>) => void calls.push(`show(${ids(refs)})`),
        fitBox: (min: readonly number[], max: readonly number[]) => (calls.push(`fitBox(${[...min, ...max].join(",")})`), true),
        overlays: () => ({
            addLines: (spec: OverlayLines) => lines.push({spec, segments: [...spec.segments], removed: false}),
            addPoints: () => 0,
            setPositions: (handle: number, positions: Float32Array) => void (lines[handle - 1].segments = [...positions]),
            remove: (handle: number) => void (lines[handle - 1].removed = true),
            clear: () => {},
            dispose: () => {},
        }),
        billboards: () => ({
            add: (b: Billboard) => (labels.set(labels.size + 1, {...b, position: [...b.position] as [number, number, number]}), labels.size),
            update: (handle: number, b: Billboard) => void labels.set(handle, {...b, position: [...b.position] as [number, number, number]}),
            remove: (handle: number) => void labels.delete(handle),
            clear: () => labels.clear(),
            onClick: () => () => {},
            dispose: () => {},
        }),
        results: {
            createResultMesh: (data: ResultMeshData, opts?: {replaceModel?: boolean}): ResultMeshLayer => {
                const r: Recorded = {data, replaceModel: !!opts?.replaceModel, colors: [], displacement: [], influence: 0, colorsVisible: true, visible: true, removed: false};
                layers.push(r);
                return {
                    vertexCount: data.positions.length / 3,
                    setColors: (rgb) => void (r.colors = [...rgb]),
                    setDisplacement: (xyz) => void (r.displacement = [...xyz]),
                    setInfluence: (v) => void (r.influence = v),
                    setColorsVisible: (v) => void (r.colorsVisible = v),
                    setVisible: (v) => void (r.visible = v),
                    remove: () => void (r.removed = true),
                };
            },
        },
    } as unknown as ViewportApi;
    return {layers, hidden, lines, labels, calls, withdraw: setActiveViewport(api)};
}

/** The colour of `t` (0..1 of the scale) in the colour map the user has chosen. */
function colorAt(t: number): number[] {
    const out = new Float32Array(3);
    getColormap(useFeaAnimationStore.getState().colormap)(t, out, 0);
    return [...out];
}

/** One beam, element 20, from node 1 to node 2: its section as one triangle on the beam's axis,
 *  with a vertex at each end and one half way. What the compute capability expands the bake's
 *  compact beam solids into. */
const compute = {
    expandBeamSolids: async () => ({
        positions: new Float32Array([3, 0, 0, 3, 2, 0, 3, 4, 0]),
        indices: new Uint32Array([0, 1, 2]),
        nVerts: 3,
        elemLabel: new Uint32Array([20]),
        elemTriStart: new Uint32Array([0]),
        elemTriCount: new Uint32Array([1]),
        node0: new Uint32Array([1, 1, 1]),
        node1: new Uint32Array([2, 2, 2]),
        t: new Float32Array([0, 0.5, 1]),
    }),
};
const BEAM_MESH = {elements_url: "fea.mesh.elements.beam.bin", beam_solids_compact_url: "fea.beam_solids.compact.bin"};
/** The stress field with a second bucket: the beam, element 20, carrying 20. */
const stressWithBeam = (): FeaManifestField => {
    const field = stress();
    field.per_type!.push({
        elem_type: "line", n_elements: 1, n_ips: 1, ip_layout: [], element_labels: [20], element_node_indices: [[1, 2]],
        blob: {url: "fea.stress.line.bin", header_bytes: 1024, stride_bytes: 4, dtype: "float32", byte_order: "little"},
        scalar_range: {S: [0, 100]},
    } as unknown as NonNullable<FeaManifestField["per_type"]>[number]);
    return field;
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

const load = (field: FeaManifestField | null, over: Partial<Parameters<typeof loadFeaOnLayers>[0]> = {}) =>
    loadFeaOnLayers(
        {
            sourceName: "deck.frd",
            manifest: manifest(field ? [field] : []),
            fieldName: field?.name_canonical ?? null,
            stepIndex: 0,
            reduction: field ? "magnitude" : null,
            ...over,
        },
        {fea, compute},
    );

let withdraw: (() => void) | null = null;
afterEach(() => {
    closeLayerSession();
    useFeaAnimationStore.getState().reset();
    // The deformation scale is a preference that outlives a reset: back to the automatic one.
    useFeaAnimationStore.setState({scaleFactor: 1, scaleFactorAuto: true, beamSolidsVisible: false});
    withdraw?.();
    withdraw = null;
});

test("the mesh is drawn from the bake's arrays and the field painted on it", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {sliderFactor: 1});

    assert.equal(viewport.layers.length, 1);
    const [faces] = viewport.layers;
    assert.equal(faces.replaceModel, true, "the result is what the viewport shows now");
    assert.deepEqual([...faces.data.positions], [0, 0, 0, 3, 0, 0, 3, 4, 0, 0, 4, 0]);
    assert.deepEqual([...faces.data.indices!], [0, 1, 2, 0, 2, 3]);
    assert.equal(faces.data.topology, "triangles");

    // The scale is measured over this step's values on the drawn nodes: 0 .. 1.
    assert.deepEqual(faces.colors.slice(0, 3), colorAt(0));
    assert.deepEqual(faces.colors.slice(6, 9), colorAt(1));
    assert.deepEqual(faces.displacement, [...STEPS[0]]);

    const store = useFeaAnimationStore.getState();
    assert.equal(store.sessionActive, true);
    assert.equal(store.mesh, null, "no three.js mesh stands behind it");
    assert.ok(store.resultLayer);
    assert.equal(store.nSteps, 2);
    assert.equal(store.fieldName, "displacement");
    assert.deepEqual(store.range, [-1, 1], "a mode shape sweeps both ways");
    assert.equal(store.legendScopeLabel, "shown elements");
    const legend = useColorStore.getState();
    assert.equal(legend.showLegend, true);
    assert.deepEqual([legend.min, legend.max], [0, 1]);
    // Drawn where the controls say: the slider times the deformation scale chosen for the model.
    assert.equal(faces.influence, 1 * store.scaleFactor);
    assert.ok(layerSessionActive());
});

test("a step change repaints the same layer: no new mesh, new colours and displacement", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {sliderFactor: 1});
    const [faces] = viewport.layers;
    // The user's own slider position and deformation scale.
    useFeaAnimationStore.getState().setFactor(0.5);
    useFeaAnimationStore.getState().setScaleFactor(4);

    stepResults(1);
    await useFeaAnimationStore.getState().applyStep!(1);
    assert.equal(viewport.layers.length, 1);
    assert.equal(useFeaAnimationStore.getState().stepIndex, 1);
    assert.deepEqual(faces.displacement, [...STEPS[1]]);
    assert.deepEqual(faces.colors.slice(6, 9), colorAt(1), "node 2 is this step's maximum too");
    assert.deepEqual(faces.colors.slice(9, 12), colorAt(0.25));
    assert.equal(faces.influence, 2, "the slider and the scale the user has now, not the first load's");
    assert.deepEqual([useColorStore.getState().min, useColorStore.getState().max], [0, 2]);

    // Past the end wraps to the first step; a step out of range is clamped.
    stepResults(1);
    assert.equal(useFeaAnimationStore.getState().stepIndex, 0);
    setResultStep(99);
    assert.equal(useFeaAnimationStore.getState().stepIndex, 1);
    await useFeaAnimationStore.getState().applyStep!(1);
});

test("playing sweeps the layer's deformation scale and touches no vertex data", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {sliderFactor: 1});
    const [faces] = viewport.layers;
    const painted = {colors: faces.colors, displacement: faces.displacement};
    useFeaAnimationStore.getState().setScaleFactor(10);
    useFeaAnimationStore.setState({isPlaying: true, period: 2});
    const seen: number[] = [];
    let phase = 0;
    for (let i = 0; i < 8; i++) {
        phase = stepFeaSweep(useFeaAnimationStore.getState(), phase, 0.25)!.phase;
        seen.push(faces.influence);
    }
    assert.ok(Math.max(...seen) > 9.9 && Math.min(...seen) < -9.9, `swept ${seen.join(", ")}`);
    assert.equal(faces.colors, painted.colors);
    assert.equal(faces.displacement, painted.displacement);
});

test("the warp switched off leaves the shape undeformed and the colours as they were", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    useFeaAnimationStore.setState({warpEnabled: false});
    try {
        await load(displacement(), {sliderFactor: 1});
        const [faces] = viewport.layers;
        assert.deepEqual(faces.displacement, new Array(12).fill(0));
        assert.deepEqual(faces.colors.slice(6, 9), colorAt(1));
    } finally {
        useFeaAnimationStore.setState({warpEnabled: true});
    }
});

test("the result colours switch follows the store", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement());
    const [faces] = viewport.layers;
    useFeaAnimationStore.getState().setResultColorsVisible(false);
    assert.equal(faces.colorsVisible, false);
    useFeaAnimationStore.getState().setResultColorsVisible(true);
    assert.equal(faces.colorsVisible, true);
});

test("a mesh without fields is drawn with no result session", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(null);
    assert.equal(viewport.layers.length, 1);
    assert.deepEqual(viewport.layers[0].colors, []);
    assert.equal(useFeaAnimationStore.getState().sessionActive, false);
    assert.equal(useColorStore.getState().showLegend, false);
});

test("a request that cannot be drawn is refused with the reason, before anything is shown", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await assert.rejects(load(displacement(), {stepIndex: 5}), /out of range/);
    assert.equal(viewport.layers.length, 0);
});

test("an element field makes the faces element-local and paints each element in its own colour", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(stress(), {manifest: manifest([displacement(), stress()]), reduction: "S", sliderFactor: 1});

    // The faces in node numbering were replaced by one vertex per triangle corner.
    const [nodeFaces, faces] = viewport.layers;
    assert.equal(nodeFaces.removed, true);
    assert.equal(faces.replaceModel, false, "the camera stays where it was");
    assert.deepEqual([...faces.data.indices!], [0, 1, 2, 3, 4, 5]);
    assert.deepEqual([...faces.data.positions], [0, 0, 0, 3, 0, 0, 3, 4, 0, 0, 0, 0, 3, 4, 0, 0, 4, 0]);
    // Element 7 (value 10) is the first triangle, element 8 (value 30) the second: the scale is
    // measured over the two, so they sit at its ends, and the shared nodes 0 and 2 carry both.
    for (const corner of [0, 1, 2]) assert.deepEqual(faces.colors.slice(corner * 3, corner * 3 + 3), colorAt(0));
    for (const corner of [3, 4, 5]) assert.deepEqual(faces.colors.slice(corner * 3, corner * 3 + 3), colorAt(1));
    // Warped by the displacement field's step, node by node: node 2 is corners 2 and 4.
    assert.deepEqual(faces.displacement, [0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0]);
    assert.equal(faces.influence, useFeaAnimationStore.getState().scaleFactor);
    assert.deepEqual([useColorStore.getState().min, useColorStore.getState().max], [10, 30]);
    assert.equal(useFeaAnimationStore.getState().fieldName, "stress");

    // A nodal field afterwards is painted through the same element-local faces: no new mesh.
    await load(displacement(), {manifest: manifest([displacement(), stress()])});
    assert.equal(viewport.layers.length, 2);
    assert.deepEqual(faces.colors.slice(6, 9), colorAt(1), "node 2, the step's maximum, as corner 2");
    assert.deepEqual(faces.colors.slice(12, 15), colorAt(1), "and as corner 4");
    assert.deepEqual(faces.colors.slice(0, 3), colorAt(0));
});

test("an element field averaged onto the nodes keeps the faces in node numbering", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    useFeaAnimationStore.setState({nodalAverage: true});
    try {
        await load(stress(), {manifest: manifest([displacement(), stress()]), reduction: "S"});
        assert.equal(viewport.layers.length, 1);
        const [faces] = viewport.layers;
        // Nodes 0 and 2 belong to both elements (the mean, 20); node 1 to element 7, node 3 to 8.
        assert.deepEqual(faces.colors.slice(0, 3), colorAt(0.5));
        assert.deepEqual(faces.colors.slice(3, 6), colorAt(0));
        assert.deepEqual(faces.colors.slice(9, 12), colorAt(1));
    } finally {
        useFeaAnimationStore.setState({nodalAverage: false});
    }
});

test("a field with result points draws a marker for each, coloured and moved with the nodes", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    const points = stress({name_canonical: "points", support: "result_point"});
    await load(points, {manifest: manifest([displacement(), points]), reduction: "S"});
    const markers = viewport.layers.find((l) => l.data.topology === "points")!;
    assert.ok(markers, "a layer of points");
    assert.equal(markers.data.sizePx, 7);
    // One result point per element, at the middle of its three nodes.
    assert.deepEqual([...markers.data.positions].map((v) => Math.round(v * 1000) / 1000), [2, 1.333, 0, 1, 2.667, 0]);
    assert.deepEqual(markers.colors.slice(0, 3), colorAt(0));
    assert.deepEqual(markers.colors.slice(3, 6), colorAt(1));
    // Node 2 moves 1 in z and belongs to both: each point moves a third of that.
    assert.deepEqual(markers.displacement.map((v) => Math.round(v * 1000) / 1000), [0, 0, 0.333, 0, 0, 0.333]);

    // Switched off they leave the screen, and so they do with the result colours.
    useFeaAnimationStore.getState().setResultPointMarkersVisible(false);
    assert.equal(markers.visible, false);
    useFeaAnimationStore.getState().setResultPointMarkersVisible(true);
    assert.equal(markers.visible, true);

    // A nodal field has none.
    await load(displacement(), {manifest: manifest([displacement(), points])});
    assert.equal(markers.removed, true);
});

test("the undeformed outline is the element edges where they were, while it is asked for", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {manifest: manifest([displacement()], {edges_url: "fea.mesh.edges.bin"})});
    assert.equal(viewport.layers.length, 2, "faces and element edges");
    useFeaAnimationStore.getState().setShowUndeformed(true);
    try {
        const ghost = viewport.layers[2];
        assert.ok(ghost, "a third layer");
        assert.equal(ghost.data.topology, "lines");
        assert.ok(ghost.data.opacity! < 1);
        assert.deepEqual(ghost.displacement, [], "never displaced");
        useFeaAnimationStore.getState().setShowUndeformed(false);
        assert.equal(ghost.removed, true);
    } finally {
        useFeaAnimationStore.getState().setShowUndeformed(false);
    }
});

test("the elements are the viewport's objects and the Scene tree's rows, by the same ids", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    const announced: {model: string; extras?: Record<string, unknown>}[] = [];
    const off = onModelLoaded((info) => announced.push(info));
    try {
        await load(stress(), {manifest: manifest([displacement(), stress()]), reduction: "S"});
    } finally {
        off();
    }
    const [nodeFaces, faces] = viewport.layers;
    for (const layer of [nodeFaces, faces]) {
        const objects = layer.data.objects!;
        assert.equal(objects.model, "fea-result:deck.frd");
        assert.deepEqual([...objects.ids], ["E7", "E8"]);
        // Element 7 is the first triangle of the index list, element 8 the second, in either numbering.
        assert.deepEqual([...objects.segments], [0, 3, 3, 3]);
        assert.deepEqual([...objects.objectOf], [0, 1]);
    }
    assert.equal(announced.length, 1);
    assert.equal(announced[0].model, "fea-result:deck.frd");
    assert.deepEqual(announced[0].extras, {
        id_hierarchy: {"fea-root": ["FEA elements", "*"], E7: ["E7", "fea-root"], E8: ["E8", "fea-root"]},
        draw_ranges_node0: {E7: [0, 3], E8: [3, 3]},
    });
});

test("hiding elements takes their edges with them and measures the scale over what is left", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(stress(), {manifest: manifest([displacement(), stress()], {edges_url: "fea.mesh.edges.bin"}), reduction: "S"});
    const edges = viewport.layers.find((l) => l.data.topology === "lines")!;
    assert.deepEqual([...edges.data.indices!], [0, 1, 1, 2, 2, 3, 3, 0]);
    assert.deepEqual([useColorStore.getState().min, useColorStore.getState().max], [10, 30]);

    viewport.hidden.add("E8");
    useVisibilityStore.getState().bump();
    await new Promise((resolve) => setTimeout(resolve, 20));
    // Element 8 owned the edges 2-3 and 3-0: they are drawn as nothing, the others as before,
    // displaced as the nodes are.
    assert.equal(edges.removed, true);
    const masked = viewport.layers.filter((l) => l.data.topology === "lines").at(-1)!;
    assert.deepEqual([...masked.data.indices!], [0, 1, 1, 2, 2, 2, 3, 3]);
    assert.deepEqual(masked.displacement, [...STEPS[0]]);
    // Element 7's value alone (a range of one value is widened a hair, so it has two ends).
    assert.ok(Math.abs(useColorStore.getState().min - 10) < 1e-3 && Math.abs(useColorStore.getState().max - 10) < 1e-3);

    viewport.hidden.clear();
    useVisibilityStore.getState().bump();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const shown = viewport.layers.filter((l) => l.data.topology === "lines").at(-1)!;
    assert.deepEqual([...shown.data.indices!], [0, 1, 1, 2, 2, 3, 3, 0]);
    assert.deepEqual([useColorStore.getState().min, useColorStore.getState().max], [10, 30]);
});

test("beam sections are drawn when asked for, coloured and moved as their beams' nodes are", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {manifest: manifest([displacement()], BEAM_MESH), sliderFactor: 1});
    assert.equal(viewport.layers.length, 1, "not drawn until they are asked for");

    useFeaAnimationStore.getState().setBeamSolidsVisible(true);
    await tick();
    const solids = viewport.layers[1];
    assert.ok(solids, "a layer of their own");
    assert.deepEqual([...solids.data.positions], [3, 0, 0, 3, 2, 0, 3, 4, 0]);
    // The beam is the object its element is: the third of the result's elements.
    assert.deepEqual([...solids.data.objects!.ids], ["E7", "E8", "E20"]);
    assert.deepEqual([...solids.data.objects!.segments], [0, 3]);
    assert.deepEqual([...solids.data.objects!.objectOf], [2]);
    // Node 1 carries 0 and node 2 carries 1: the section's vertices sit at 0, half way and 1.
    assert.deepEqual(solids.colors.slice(0, 3), colorAt(0));
    assert.deepEqual(solids.colors.slice(3, 6), colorAt(0.5));
    assert.deepEqual(solids.colors.slice(6, 9), colorAt(1));
    assert.deepEqual([solids.displacement[2], solids.displacement[5], solids.displacement[8]], [0, 0.5, 1]);
    assert.equal(solids.influence, viewport.layers[0].influence, "scaled with the faces");

    useFeaAnimationStore.getState().setBeamSolidsVisible(false);
    await tick();
    assert.equal(solids.visible, false);
    assert.equal(solids.removed, false, "kept for the next time");
});

test("an element field paints each beam section with its beam's value, in place of its coloured line", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    const field = stressWithBeam();
    await load(field, {manifest: manifest([displacement(), field], BEAM_MESH), reduction: "S"});
    // Sections off: the beam is a coloured line between its nodes, at the middle of the scale.
    const line = viewport.layers.find((l) => l.data.topology === "lines")!;
    assert.deepEqual([...line.data.positions], [3, 0, 0, 3, 4, 0]);
    assert.deepEqual(line.colors, [...colorAt(0.5), ...colorAt(0.5)]);
    assert.equal(line.visible, true);

    useFeaAnimationStore.getState().setBeamSolidsVisible(true);
    await tick();
    const solids = viewport.layers.filter((l) => l.data.topology === "triangles" && l.data.positions.length === 9).at(-1)!;
    assert.ok(solids, "the sections, element-local");
    assert.equal(solids.removed, false);
    for (const corner of [0, 1, 2]) assert.deepEqual(solids.colors.slice(corner * 3, corner * 3 + 3), colorAt(0.5));
    const lineNow = viewport.layers.filter((l) => l.data.topology === "lines").at(-1)!;
    assert.equal(lineNow.visible, false, "one rendering of a beam at a time");
});

test("a time history can be loaded ahead of playing it; other results offer no such thing", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement());
    assert.equal(useFeaAnimationStore.getState().startFramePreload, null, "mode shapes are not frames of one history");
    closeLayerSession();
    await load(displacement({analysis_kind: "transient"} as Partial<FeaManifestField>));
    const start = useFeaAnimationStore.getState().startFramePreload;
    assert.ok(start, "offered for a transient field of more than one step");
    start();
    await tick();
    const progress = useFeaAnimationStore.getState().framePreload;
    assert.ok(progress && progress.loaded === progress.total, `every frame loaded: ${JSON.stringify(progress)}`);
});

test("a plugin's element colours are one more layer of the result, moving with its nodes", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {sliderFactor: 1, displacementScale: 2});
    assert.equal(overlays.hasFeaModel(), true);

    const painted = overlays.paintFeaElements("p", {colors: new Map([[8, "#ff0000"]])});
    assert.ok(painted);
    const layer = viewport.layers.at(-1)!;
    // Element 8 alone, a vertex per corner (nodes 0, 2, 3), drawn over the faces.
    assert.deepEqual([...layer.data.positions], [0, 0, 0, 3, 4, 0, 0, 4, 0]);
    assert.ok(layer.data.overlay, "an annotation, in front of the faces it lies on");
    assert.deepEqual(layer.colors, [1, 0, 0, 1, 0, 0, 1, 0, 0]);
    // It starts where the result is: node 2 moved 1 in z at this step, scaled as the faces are.
    assert.deepEqual(layer.displacement, [0, 0, 0, 0, 0, 1, 0, 0, 0]);
    assert.equal(layer.influence, viewport.layers[0].influence);

    await useFeaAnimationStore.getState().applyStep!(1);
    assert.deepEqual(layer.displacement, [0, 0, 0, 0, 0, 2, 0, 0, 0.5], "the next step moves it with the nodes");

    // Every element in `only` that has no colour of its own takes the neutral one.
    const neutral = overlays.paintFeaElements("p", {colors: new Map(), neutral: [1, 1, 1], only: [7]});
    assert.deepEqual([...viewport.layers.at(-1)!.data.positions], [0, 0, 0, 3, 0, 0, 3, 4, 0]);
    neutral!.remove();
    assert.equal(viewport.layers.at(-1)!.removed, true);

    painted.remove();
    assert.equal(layer.removed, true);
    assert.equal(overlays.paintFeaElements("p", {colors: new Map([[8, "#ff0000"]]), on: "beamSolids"}), null, "no beam sections drawn");
});

test("an outline and a label pinned to elements stay on the deformed surface", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {sliderFactor: 1, displacementScale: 1});

    const outline = overlays.addElementOutlines("p", {elements: [7, 8], color: "#ffffff", widthPx: 3, throughModel: true});
    assert.ok(outline);
    const [drawn] = viewport.lines;
    // The four sides around the pair, not the diagonal they share.
    assert.equal(drawn.segments.length / 6, 4);
    assert.equal(drawn.spec.onTop, true);
    assert.equal(drawn.spec.widthPx, 3);
    const zs = () => drawn.segments.filter((_, i) => i % 3 === 2);
    assert.equal(Math.max(...zs()), 1, "node 2 is up by 1 at this step");

    overlays.addLabels("p", [{atElements: [8], text: "E8"}, {position: [1, 2, 3], text: "here"}]);
    // Element 8's corners are nodes 0, 2 and 3: their centre, with node 2 up by 1.
    assert.deepEqual([...viewport.labels.get(1)!.position].map((v) => +v.toFixed(4)), [1, 2.6667, 0.3333]);
    assert.deepEqual([...viewport.labels.get(2)!.position], [1, 2, 3]);
    assert.equal(viewport.labels.get(1)!.clipped, true);

    // Another step, and the frame loop's hook: both follow.
    await useFeaAnimationStore.getState().applyStep!(1);
    runBeforeDraw();
    assert.equal(Math.max(...zs()), 2);
    assert.deepEqual([...viewport.labels.get(1)!.position].map((v) => +v.toFixed(4)), [1, 2.6667, 0.8333]);
    // Nothing moved: nothing is written again.
    const before = drawn.segments;
    runBeforeDraw();
    assert.equal(drawn.segments, before);

    assert.equal(overlays.addElementOutlines("p", {elements: [99]}), null);
    assert.equal(overlays.frameFeaElements([7]), true);
    assert.equal(viewport.calls.at(-1), "fitBox(0,0,0,3,4,2)");

    // A result that leaves the screen takes what was pinned to it along.
    closeLayerSession();
    assert.equal(drawn.removed, true);
    assert.equal(viewport.labels.size, 0);
    assert.equal(overlays.hasFeaModel(), false);
});

test("a plugin hides and isolates elements as the viewport's objects", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement());
    overlays.isolateFeaElements([8]);
    overlays.hideFeaElements([7, 8]);
    overlays.isolateFeaElements(null);
    assert.deepEqual(viewport.calls, ["isolate(E8)", "hide(E7,E8)", "showAll"]);
    viewport.hidden.add("E7");
    assert.deepEqual(overlays.hiddenFeaElements(), [7]);
});

test("a result's sets are isolated, hidden and shown as the viewport's objects", async () => {
    const {useFeaIsolationStore} = await import("@/state/feaIsolationStore");
    const {useFemConceptsStore} = await import("@/state/femConceptsStore");
    const isolation = await import("@/utils/scene/fea/layerIsolation");
    const {elementSetRowId} = await import("@/utils/scene/fea/feaTreeRows");
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement(), {manifest: manifest([displacement()], {edges_url: "fea.mesh.edges.bin"})});
    assert.equal(isolation.layerResultShown(), true);
    useFeaIsolationStore.getState().setSets("deck.frd", [
        {name: "deck", members: ["EL8"], fe_object_type: "element"},
        {name: "both", members: ["EL7", "EL8"], fe_object_type: "element"},
        {name: "elsewhere", members: ["EL99"], fe_object_type: "element"},
        {name: "nodes", members: ["P1"], fe_object_type: "node"},
    ]);
    try {
        isolation.isolateFeaRows([elementSetRowId("deck")]);
        assert.deepEqual(viewport.calls, ["isolate(E8)"]);
        assert.deepEqual(useFeaIsolationStore.getState().isolation.rows, [elementSetRowId("deck")]);
        // The rest stays as a wireframe: element 7 is hidden, and its edges are still drawn.
        viewport.hidden.add("E7");
        useVisibilityStore.getState().bump();
        await tick();
        const edges = () => viewport.layers.filter((l) => l.data.topology === "lines" && !l.removed).at(-1)!;
        assert.deepEqual([...edges().data.indices!], [0, 1, 1, 2, 2, 3, 3, 0]);
        isolation.toggleFeaContextWireframe();
        assert.deepEqual([...edges().data.indices!], [0, 0, 1, 1, 2, 3, 3, 0], "hidden outright: the edges only element 7 has are gone");

        // A set that names nothing the result has leaves it whole.
        viewport.calls.length = 0;
        isolation.isolateFeaRows([elementSetRowId("elsewhere")]);
        assert.deepEqual(viewport.calls, ["showAll"]);
        assert.equal(isolation.isolateFeaSetsMatching("BOTH"), 1);
        assert.equal(viewport.calls.at(-1), "isolate(E7,E8)");
        isolation.clearFeaIsolation();
        assert.equal(viewport.calls.at(-1), "showAll");
        assert.deepEqual(useFeaIsolationStore.getState().isolation.rows, []);

        isolation.hideFeaRows([elementSetRowId("deck")]);
        isolation.showFeaRows([elementSetRowId("both")]);
        assert.deepEqual(viewport.calls.slice(-2), ["hide(E8)", "show(E7,E8)"]);

        // The beams that only carry a mass stay hidden unless they are asked for, isolated or not.
        useFemConceptsStore.setState({dummyMassIds: ["E8"], showDummyMasses: false});
        isolation.applyLayerIsolation();
        assert.equal(viewport.calls.at(-1), "hide(E8)");
        isolation.isolateFeaRows([elementSetRowId("both")]);
        assert.equal(viewport.calls.at(-1), "isolate(E7)");
        useFemConceptsStore.setState({showDummyMasses: true});
        isolation.applyLayerIsolation();
        assert.equal(viewport.calls.at(-1), "isolate(E7,E8)");
    } finally {
        useFemConceptsStore.setState({dummyMassIds: [], showDummyMasses: false});
        useFeaIsolationStore.getState().reset(null);
    }
});

test("closing the session takes its layers off the viewport", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    await load(displacement());
    closeLayerSession();
    assert.equal(viewport.layers[0].removed, true);
    assert.equal(layerSessionActive(), false);
    closeLayerSession(); // nothing open: nothing to do
});

test("without a 3D view there is nowhere to draw", async () => {
    await assert.rejects(load(displacement()), /No 3D view/);
});
