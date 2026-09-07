import assert from "node:assert/strict";
import test from "node:test";

import {getColormap} from "@/utils/scene/fea/colormaps";
import {
    MAX_LEVELS,
    MIN_LEVELS,
    bandedColormap,
    categoryEntries,
    contourBands,
    contourTicks,
    normaliseLevels,
    resolveContourRange,
} from "@/utils/scene/fea/contourScale";

function sample(map: (t: number, out: Float32Array, offset?: number) => void, t: number): string {
    const out = new Float32Array(3);
    map(t, out);
    return Array.from(out)
        .map((v) => v.toFixed(4))
        .join(",");
}

test("an absent or empty override leaves the field's own range alone", () => {
    assert.deepEqual(resolveContourRange([1, 9], null), [1, 9]);
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: null, max: null}), [1, 9]);
});

test("either end can be pinned on its own", () => {
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: 0, max: null}), [0, 9]);
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: null, max: 250}), [1, 250]);
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: -5, max: 5}), [-5, 5]);
});

test("an inverted or empty range falls back rather than blanking the view", () => {
    // What a user has typed halfway through entering a negative minimum.
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: 10, max: 2}), [1, 9]);
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: 4, max: 4}), [1, 9]);
    assert.deepEqual(resolveContourRange([1, 9], {levels: null, min: Number.NaN, max: null}), [1, 9]);
});

test("levels are whole numbers inside the offered bounds", () => {
    assert.equal(normaliseLevels(null), null);
    assert.equal(normaliseLevels(undefined), null);
    assert.equal(normaliseLevels(9), 9);
    assert.equal(normaliseLevels(9.4), 9);
    assert.equal(normaliseLevels(0), MIN_LEVELS);
    assert.equal(normaliseLevels(1000), MAX_LEVELS);
});

test("banding collapses a range onto one colour per band", () => {
    const map = bandedColormap(getColormap("contour"), 4);
    // Everything inside a band is the same colour ...
    assert.equal(sample(map, 0.01), sample(map, 0.24));
    // ... and neighbouring bands are not.
    assert.notEqual(sample(map, 0.24), sample(map, 0.26));
    // Four bands, four distinct colours.
    const distinct = new Set([0.1, 0.35, 0.6, 0.85].map((t) => sample(map, t)));
    assert.equal(distinct.size, 4);
});

test("the top of the range belongs to the last band, not to one past it", () => {
    const map = bandedColormap(getColormap("contour"), 5);
    assert.equal(sample(map, 1), sample(map, 0.95));
    // Out of range clamps rather than going black.
    assert.equal(sample(map, 2), sample(map, 1));
    assert.equal(sample(map, -1), sample(map, 0));
});

test("no levels means the colormap is handed back untouched", () => {
    const raw = getColormap("contour");
    assert.equal(bandedColormap(raw, null), raw);
});

test("legend bands cover the range end to end, with the painter's own colours", () => {
    const bands = contourBands([0, 100], 4, "contour");
    assert.equal(bands.length, 4);
    assert.equal(bands[0].from, 0);
    assert.equal(bands[3].to, 100);
    for (let i = 1; i < bands.length; i++) {
        assert.equal(bands[i].from, bands[i - 1].to);
    }
    // A swatch is exactly what an element in that band is painted.
    const map = bandedColormap(getColormap("contour"), 4);
    const out = new Float32Array(3);
    map(0.6, out);
    const painted = `rgb(${Math.round(out[0] * 255)}, ${Math.round(out[1] * 255)}, ${Math.round(out[2] * 255)})`;
    assert.equal(bands[2].color, painted);
});

test("ticks run from the top down and include both ends", () => {
    assert.deepEqual(contourTicks([0, 10], 2), [10, 5, 0]);
    assert.deepEqual(contourTicks([0, 10], 1), [10, 0]);
});

test("categorical entries are the deck's names, sorted, with colours", () => {
    const entries = categoryEntries({"1": "S355", "2": "eq_mat_soft"}, [1, 2], "contour");
    assert.deepEqual(
        entries.map((e) => e.label),
        ["eq_mat_soft", "S355"],
    );
    assert.ok(entries.every((e) => e.color.startsWith("rgb(")));
});

test("a category not on screen is not named in the legend", () => {
    const labels = {"1": "S355", "2": "eq_mat_soft", "3": "eq_mat_stiff"};
    const entries = categoryEntries(labels, [1, 3], "contour", new Set([1, 3]));
    assert.deepEqual(
        entries.map((e) => e.label),
        ["eq_mat_stiff", "S355"],
    );
});

test("no value labels means no categorical legend", () => {
    assert.deepEqual(categoryEntries(undefined, [0, 1], "contour"), []);
});
