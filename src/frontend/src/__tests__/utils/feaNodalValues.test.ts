// A picked element's nodal values (a displacement): read at the element's nodes for the step on
// screen, through the step blob the colouring reads; and one node's (`feaValuesForNode`). Against a
// synthetic manifest: two triangles over four nodes (ids 101..104), a nodal DISPLACEMENT with two
// steps, a nodal ROTATION, and a property field that carries the connectivity.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

await import("../fixtures/panelRealm");

const {useFeaAnimationStore} = await import("@/state/feaAnimationStore");
const {getCapabilities} = await import("@/services/capabilities");
const {feaValuesForElement, feaValuesForNode} = await import("@/utils/scene/fea/elementValues");
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";

/** xyz per node, per step: node i moves by (i, 10 i, 100 i) times the step's number. */
const disp = (step: number) => Float32Array.from({length: 12}, (_, k) => Math.floor(k / 3) * [1, 10, 100][k % 3] * (step + 1));

const nodal = (name: string, url: string): FeaManifestField =>
    ({
        name_canonical: name,
        name_native: name,
        kind: "vector3",
        category: "displacement",
        support: "nodal",
        unit: "m",
        analysis_kind: "static",
        components: ["X", "Y", "Z"],
        n_steps: 2,
        steps: [{i: 0, value: 1, label: "LC1"}, {i: 1, value: 2, label: "LC2"}],
        blob: {url, header_bytes: 0, stride_bytes: 48, dtype: "float32", byte_order: "little"},
    }) as unknown as FeaManifestField;

const property = {
    name_canonical: "props.thickness",
    name_native: "T",
    kind: "scalar",
    category: "property",
    support: "element_average",
    analysis_kind: "static",
    components: ["T"],
    steps: [{i: 0, value: 1, label: "model"}],
    n_steps: 1,
    per_type: [{elem_type: "triangle", n_elements: 2, n_ips: 1, element_labels: [7, 8], element_node_indices: [[0, 1, 2], [0, 2, 3]]}],
} as unknown as FeaManifestField;

const manifest = {
    fields: [property, nodal("DISPLACEMENT", "disp.bin"), nodal("ROTATION", "rot.bin")],
    mesh: {url: "m.glb", n_points: 4, n_cells: 2, node_labels: [101, 102, 103, 104]},
} as unknown as FeaManifest;

const fetched: string[] = [];
const caps = getCapabilities() as unknown as Record<string, unknown>;
const realFea = caps.fea;
function install(): void {
    Object.defineProperty(caps, "fea", {
        configurable: true,
        value: {
            ...(realFea as object),
            fieldFetchers: () => ({
                cacheKey: `nodal-${Math.random()}`,
                fetcher: async (url: string) => {
                    throw new Error(`no whole-blob fetch expected (${url})`);
                },
                rangeFetcher: async (url: string, start: number) => {
                    fetched.push(url);
                    const step = start / 48;
                    const values = url === "rot.bin" ? disp(step).map((v) => -v) : disp(step);
                    return {buf: values.buffer, ranged: true};
                },
            }),
        },
    });
}
afterEach(() => {
    Object.defineProperty(caps, "fea", {configurable: true, value: realFea, writable: true});
    fetched.length = 0;
});

function show(fieldName: string, stepIndex: number): void {
    useFeaAnimationStore.setState({manifest, sourceName: "deck.SIN", fieldName, stepIndex} as never);
}

test("the displayed nodal attribute at a picked element's nodes, for the step on screen", async () => {
    install();
    show("DISPLACEMENT", 1);
    const r = await feaValuesForElement("E8", "active", {nodal: true});
    assert.equal(r.fields.length, 1, "the displayed attribute only: one blob read");
    assert.deepEqual(fetched, ["disp.bin"]);
    const f = r.fields[0];
    assert.equal(f.kind, "nodal");
    if (f.kind !== "nodal") return;
    assert.equal(f.attribute, "DISPLACEMENT");
    assert.equal(f.position, "nodal");
    assert.equal(f.unit, "m");
    assert.deepEqual(f.components, ["X", "Y", "Z"]);
    // Element 8 is nodes 0, 2, 3 -- ids 101, 103, 104 -- at step 1 (twice step 0).
    assert.deepEqual(f.nodes, [
        {node: 101, values: [0, 0, 0]},
        {node: 103, values: [4, 40, 400]},
        {node: 104, values: [6, 60, 600]},
    ]);
    assert.equal(f.stepIndex, 1);
    assert.equal(f.stepLabel, "LC2");
});

test("without {nodal: true}, a nodal attribute gives nothing (the element reader as it was)", async () => {
    install();
    show("DISPLACEMENT", 0);
    const r = await feaValuesForElement("E8", "active");
    assert.deepEqual(r.fields, []);
    assert.deepEqual(fetched, []);
});

test("scope all reads every nodal attribute, in manifest order", async () => {
    install();
    show("DISPLACEMENT", 0);
    const r = await feaValuesForElement("E7", "all", {nodal: true});
    const nodalFields = r.fields.filter((f) => f.kind === "nodal");
    assert.deepEqual(nodalFields.map((f) => (f.kind === "nodal" ? f.attribute : "")), ["DISPLACEMENT", "ROTATION"]);
    const rot = nodalFields[1];
    if (rot.kind === "nodal") assert.deepEqual(rot.nodes.map((n) => n.node), [101, 102, 103]);
    // The property field is an element field: its kind says so.
    assert.ok(r.fields.every((f) => f.kind === "element" || f.kind === "nodal"));
});

test("one node: feaValuesForNode by its solver id", async () => {
    install();
    show("DISPLACEMENT", 0);
    const r = await feaValuesForNode(103);
    assert.equal(r.node, 103);
    assert.equal(r.fields.length, 1);
    assert.deepEqual(r.fields[0].nodes, [{node: 103, values: [2, 20, 200]}]);
    assert.equal(r.stepLabel, "LC1");
    assert.deepEqual((await feaValuesForNode("N104", "all")).fields.map((f) => f.attribute), ["DISPLACEMENT", "ROTATION"]);
    assert.deepEqual((await feaValuesForNode(999)).fields, [], "not a node of the model");
    assert.deepEqual((await feaValuesForNode(101, "properties")).fields, [], "properties have no nodal attributes");
});
