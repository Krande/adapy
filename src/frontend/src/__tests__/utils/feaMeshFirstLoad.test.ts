import {describe, it} from "node:test";
import assert from "node:assert/strict";

import type {FeaManifest} from "../../services/viewerApi";
import {feaOpening, type FeaOpenState} from "../../utils/scene/fea/streaming/meshFirst";

// Mesh first, from the loader's side (loadWithDefaults -> feaOpening): the model
// opens from the bake's first preliminary manifest (no field yet: the mesh
// only), fields are added as later manifests bring them, and a field the user
// picked meanwhile stays picked through the final manifest.

const field = (name: string, category = "displacement") => ({
    name_canonical: name,
    name_native: name,
    kind: "vector3",
    category,
    support: "nodal",
    analysis_kind: "static",
    components: ["X", "Y", "Z"],
    n_steps: 3,
    steps: [0, 1, 2].map((i) => ({i, value: i + 1, label: String(i + 1)})),
    scalar_range: {magnitude: [0, 1]},
    default_view: {reduction: "magnitude", colormap: "viridis"},
});

const manifest = (fields: ReturnType<typeof field>[], preliminary: boolean): FeaManifest =>
    ({
        version: 2,
        bake_version: 6,
        ...(preliminary ? {preliminary: true} : {}),
        src: "deck.sin",
        mesh: {url: "fea.mesh.glb", n_points: 4, n_cells: 1},
        fields,
    }) as unknown as FeaManifest;

const state = (over: Partial<FeaOpenState> = {}): FeaOpenState => ({
    sourceName: null,
    fieldName: null,
    stepIndex: 0,
    reduction: "magnitude",
    factor: 1,
    scaleFactor: 1,
    ...over,
});

const U = field("U");
const S = field("S", "stress");

describe("mesh-first FEA opening", () => {
    it("opens the mesh alone from a preliminary manifest with no field yet", () => {
        const open = feaOpening(manifest([], true), "deck.sin", false, state());
        assert.equal(open.field, null);
        assert.equal(open.reduction, null);
    });

    it("opens the first field to land, then keeps showing it as more land", () => {
        // Mesh shown, nothing painted: the first field to arrive is opened.
        const first = feaOpening(manifest([U], true), "deck.sin", true, state({sourceName: "deck.sin"}));
        assert.equal(first.field?.name_canonical, "U");
        // U is painted at step 1; S lands: U stays, at its step.
        const shown = state({sourceName: "deck.sin", fieldName: "U", stepIndex: 1});
        const next = feaOpening(manifest([U, S], true), "deck.sin", true, shown);
        assert.equal(next.field?.name_canonical, "U");
        assert.equal(next.stepIndex, 1);
    });

    it("keeps the field, step, component and warp the user picked while the bake ran", () => {
        const picked = state({sourceName: "deck.sin", fieldName: "S", stepIndex: 2, reduction: "SXX", factor: 0.5, scaleFactor: 40});
        const open = feaOpening(manifest([U, S], false), "deck.sin", true, picked);
        assert.equal(open.field?.name_canonical, "S");
        assert.equal(open.stepIndex, 2);
        assert.equal(open.reduction, "SXX");
        assert.equal(open.displacementScale, 20);
    });

    it("a finished bake (nothing shown before) opens as it always did: the default field", () => {
        const leftover = state({sourceName: "deck.sin", fieldName: "S", stepIndex: 2});
        const open = feaOpening(manifest([U, S], false), "deck.sin", false, leftover);
        assert.equal(open.field?.name_canonical, "U");
        assert.equal(open.displacementScale, 1);
    });
});
