import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {planeConstant, planePosition, sliderRange, sliderStep} from "@/shell/sectionRange";

const box = (n: number) => ({min: {x: -n, y: -n, z: -n}, max: {x: n, y: n, z: n}});

describe("plane position", () => {
    test("position and constant are inverses", () => {
        // They have opposite signs, and getting that backwards makes the slider move the
        // plane the wrong way — a bug you can only see in 3D, never in a type.
        for (const v of [0, 3.5, -12, 0.001]) {
            assert.equal(planePosition(planeConstant(v)), v);
            assert.equal(planeConstant(planePosition(v)), v);
        }
    });
});

describe("sliderRange", () => {
    test("spans the box on the normal, with padding past both ends", () => {
        const [lo, hi] = sliderRange([1, 0, 0], box(10));
        // ±10 projected, padded by 10% of the 20-unit extent.
        assert.equal(lo, -12);
        assert.equal(hi, 12);
    });

    test("the range clears the model at both extremes", () => {
        // The padding is the whole point: without it the slider ends with the plane
        // exactly touching the box, so you can never fully reveal or fully clip. The last
        // sliver staying reads as a broken slider, not as a range stopping short.
        const bb = box(10);
        const [lo, hi] = sliderRange([1, 0, 0], bb);
        assert.ok(lo < bb.min.x, "low end does not clear the model");
        assert.ok(hi > bb.max.x, "high end does not clear the model");
    });

    test("stays symmetric about the box centre", () => {
        // A plane is added at the centre, so it must land at the slider midpoint —
        // otherwise a fresh plane appears with its handle off to one side.
        const [lo, hi] = sliderRange([0, 0, 1], {min: {x: 0, y: 0, z: 4}, max: {x: 2, y: 2, z: 10}});
        assert.equal((lo + hi) / 2, 7);
    });

    test("a diagonal normal projects, rather than taking one axis", () => {
        const s = Math.SQRT1_2;
        const [lo, hi] = sliderRange([s, s, 0], box(1));
        // The far corner projects to √2, not to 1.
        assert.ok(hi > Math.SQRT2, `expected past ${Math.SQRT2}, got ${hi}`);
    });

    test("no model gives a usable range instead of NaN", () => {
        assert.deepEqual(sliderRange([1, 0, 0], null), [-100, 100]);
    });

    test("a flat model still gets travel", () => {
        // A plane normal to a zero-thickness model projects every corner to the same
        // value. Padding of 10% of zero is zero, so the fallback keeps lo < hi — a range
        // of width zero freezes the input entirely.
        const [lo, hi] = sliderRange([0, 0, 1], {min: {x: 0, y: 0, z: 5}, max: {x: 4, y: 4, z: 5}});
        assert.ok(hi > lo, "flat model produced an empty range");
    });
});

describe("sliderStep", () => {
    test("divides the travel into 500", () => {
        assert.equal(sliderStep(0, 500), 1);
    });

    test("never returns zero", () => {
        // step=0 makes a range input refuse to move at all.
        assert.ok(sliderStep(7, 7) > 0);
    });
});
