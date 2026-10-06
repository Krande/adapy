import assert from "node:assert/strict";
import test from "node:test";

import type {FeaManifestField} from "@/services/viewerApi";
import {getColormap} from "@/utils/scene/fea/colormaps";
import {
    MAX_LISTED_VALUES,
    categoryCodes,
    categoryColor,
    isPropertyField,
    propertyColormap,
    propertyLegendEntries,
} from "@/utils/scene/fea/propertyColors";
import {defaultResultField} from "@/utils/scene/fea/streaming/defaultField";

// Model properties are painted in their own scheme. These pin what that means:
// distinct colours per category whatever ids the deck uses, a sequential map
// for a numeric property, and nothing at all for a result field.

function field(over: Partial<FeaManifestField>): FeaManifestField {
    return {
        name_canonical: "props.material",
        category: "property",
        kind: "scalar",
        components: ["MATERIAL"],
        n_steps: 1,
        scalar_range: {MATERIAL: [1, 3]},
        default_view: {reduction: "MATERIAL", colormap: "viridis", layer: "top", ip_reduction: "max_abs"},
        ...over,
    } as unknown as FeaManifestField;
}

function rgb(map: (t: number, out: Float32Array, offset?: number) => void, t: number): string {
    const out = new Float32Array(3);
    map(t, out);
    return Array.from(out)
        .map((v) => v.toFixed(4))
        .join(",");
}

function swatch(rank: number): string {
    const out = new Float32Array(3);
    categoryColor(rank, out);
    return Array.from(out)
        .map((v) => v.toFixed(4))
        .join(",");
}

const material = field({value_labels: {"1": "S355", "2": "soft", "3": "stiff"}});
// Section ids as a deck numbers them: sparse, and two of them adjacent.
const section = field({
    name_canonical: "props.section",
    components: ["SECTION"],
    scalar_range: {SECTION: [3, 40]},
    value_labels: {"3": "HP220x10", "4": "T300", "40": "SHS200x10"},
});
const thickness = field({
    name_canonical: "props.thickness",
    components: ["TH"],
    scalar_range: {TH: [0.008, 0.012]},
    unit: "m",
});

test("only category-property fields are properties", () => {
    assert.equal(isPropertyField(material), true);
    assert.equal(isPropertyField(field({category: "stress"} as Partial<FeaManifestField>)), false);
    assert.equal(isPropertyField(null), false);
    assert.equal(propertyColormap(field({category: "stress"} as Partial<FeaManifestField>), [0, 1]), null);
});

test("category codes come back ascending, numerically", () => {
    assert.deepEqual(categoryCodes(section), [3, 4, 40]);
    assert.deepEqual(categoryCodes(thickness), []);
});

test("each category gets its own colour by rank, however the deck numbers them", () => {
    const map = propertyColormap(section, [3, 40]);
    assert.ok(map);
    const at = (code: number) => rgb(map, (code - 3) / (40 - 3));
    // Ids 3 and 4 are neighbours on a ramp; here they are ranks 0 and 1.
    assert.equal(at(3), swatch(0));
    assert.equal(at(4), swatch(1));
    assert.equal(at(40), swatch(2));
    assert.notEqual(at(3), at(4));
});

test("no two category ranks share a colour, even past the fixed palette", () => {
    const seen = new Set<string>();
    for (let rank = 0; rank < 60; rank++) seen.add(swatch(rank));
    assert.equal(seen.size, 60);
});

test("a numeric property uses its own sequential map, not the result colormap", () => {
    const map = propertyColormap(thickness, [0.008, 0.012]);
    assert.ok(map);
    assert.equal(rgb(map, 0.5), rgb(getColormap("viridis"), 0.5));
});

test("a numeric property with known values is coloured by rank, so one outlier cannot flatten the rest", () => {
    // Plate sizes as a deck has them: many thin ones and one thick insert.
    const levels = [0.008, 0.01, 0.012, 0.2];
    const range: [number, number] = [0.008, 0.2];
    const map = propertyColormap(thickness, range, levels)!;
    const seq = getColormap("viridis");
    const at = (v: number) => rgb(map, (v - range[0]) / (range[1] - range[0]));
    assert.equal(at(0.008), rgb(seq, 0));
    assert.equal(at(0.01), rgb(seq, 1 / 3));
    assert.equal(at(0.012), rgb(seq, 2 / 3));
    assert.equal(at(0.2), rgb(seq, 1));
    // Linear, 8 mm and 12 mm would be 2 % of the scale apart.
    assert.notEqual(at(0.01), at(0.012));
    // One value: the middle of the map, not an end.
    assert.equal(rgb(propertyColormap(thickness, [0.01, 0.01], [0.01])!, 0), rgb(seq, 0.5));
});

test("the legend lists categories by name, coloured as painted, only those on screen", () => {
    const entries = propertyLegendEntries(material, [1, 3], new Set([1, 3]));
    assert.deepEqual(
        entries?.map((e) => e.label),
        ["S355", "stiff"],
    );
    const map = propertyColormap(material, [1, 3])!;
    const out = new Float32Array(3);
    map(1, out);
    const css = `rgb(${Math.round(out[0] * 255)}, ${Math.round(out[1] * 255)}, ${Math.round(out[2] * 255)})`;
    assert.equal(entries?.find((e) => e.label === "stiff")?.color, css);
});

test("a numeric property lists its painted values with the unit, or falls back to a gradient", () => {
    const entries = propertyLegendEntries(thickness, [0.008, 0.012], new Set([0.012, 0.008, 0.01]));
    assert.deepEqual(
        entries?.map((e) => e.label),
        ["0.008 m", "0.01 m", "0.012 m"],
    );
    assert.equal(propertyLegendEntries(thickness, [0.008, 0.012], null), null);
    const many = new Set(Array.from({length: MAX_LISTED_VALUES + 1}, (_, i) => 0.001 * (i + 1)));
    assert.equal(propertyLegendEntries(thickness, [0.001, 0.03], many), null);
});

test("a fresh load opens on displacement, then any result, never a property", () => {
    const disp = field({name_canonical: "u", category: "displacement"} as Partial<FeaManifestField>);
    const stress = field({name_canonical: "s", category: "stress"} as Partial<FeaManifestField>);
    assert.equal(defaultResultField([material, stress, disp])?.name_canonical, "u");
    assert.equal(defaultResultField([material, stress])?.name_canonical, "s");
    // A model-only bake: its only fields are properties, so it opens mesh-only.
    assert.equal(defaultResultField([material, section, thickness]), null);
    assert.equal(defaultResultField([]), null);
    assert.equal(defaultResultField(undefined), null);
});
