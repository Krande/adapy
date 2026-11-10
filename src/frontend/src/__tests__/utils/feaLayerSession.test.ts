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
const {setActiveViewport} = await import("@/viewport/api/viewportApi");
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

const manifest = (fields: FeaManifestField[]): FeaManifest =>
    ({version: 2, src: "deck.frd", mesh: {url: "fea.mesh.glb", n_points: 4, n_cells: 2}, fields}) as unknown as FeaManifest;

const fea: FeaCapability = {
    supports: () => true,
    fetchManifest: async () => {
        throw new Error("not asked for in this test");
    },
    fieldFetchers: () => ({
        cacheKey: `test-${Math.random()}`,
        fetcher: async (url: string) => {
            if (url === "fea.mesh.glb") return meshGlb();
            throw new Error(`unexpected fetch of ${url}`);
        },
        rangeFetcher: async (_url: string, start: number) => {
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
    removed: boolean;
}

/** A viewport that records what it is asked to draw. */
function fakeViewport(): {layers: Recorded[]; withdraw: () => void} {
    const layers: Recorded[] = [];
    const api = {
        kind: "native",
        results: {
            createResultMesh: (data: ResultMeshData, opts?: {replaceModel?: boolean}): ResultMeshLayer => {
                const r: Recorded = {data, replaceModel: !!opts?.replaceModel, colors: [], displacement: [], influence: 0, colorsVisible: true, removed: false};
                layers.push(r);
                return {
                    vertexCount: data.positions.length / 3,
                    setColors: (rgb) => void (r.colors = [...rgb]),
                    setDisplacement: (xyz) => void (r.displacement = [...xyz]),
                    setInfluence: (v) => void (r.influence = v),
                    setColorsVisible: (v) => void (r.colorsVisible = v),
                    setVisible() {},
                    remove: () => void (r.removed = true),
                };
            },
        },
    } as unknown as ViewportApi;
    return {layers, withdraw: setActiveViewport(api)};
}

/** The colour of `t` (0..1 of the scale) in the colour map the user has chosen. */
function colorAt(t: number): number[] {
    const out = new Float32Array(3);
    getColormap(useFeaAnimationStore.getState().colormap)(t, out, 0);
    return [...out];
}

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
        {fea},
    );

let withdraw: (() => void) | null = null;
afterEach(() => {
    closeLayerSession();
    useFeaAnimationStore.getState().reset();
    // The deformation scale is a preference that outlives a reset: back to the automatic one.
    useFeaAnimationStore.setState({scaleFactor: 1, scaleFactorAuto: true});
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

test("what is not drawn through layers yet is refused with the reason, before anything is shown", async () => {
    const viewport = fakeViewport();
    withdraw = viewport.withdraw;
    const element = displacement({name_canonical: "stress", category: "stress", per_type: [{}]} as unknown as Partial<FeaManifestField>);
    await assert.rejects(load(element), /element field/);
    assert.equal(viewport.layers.length, 0);
    await assert.rejects(load(displacement(), {stepIndex: 5}), /out of range/);
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
