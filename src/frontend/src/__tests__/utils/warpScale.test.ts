import assert from "node:assert/strict";
import {describe, test} from "node:test";

import {autoWarpScale, peakDisplacement} from "@/utils/scene/fea/warpScale";
import type {FeaManifestField} from "@/services/viewerApi";

const field = (scalar_range: Record<string, [number, number]>): FeaManifestField =>
    ({
        name_canonical: "disp",
        components: Object.keys(scalar_range),
        scalar_range,
    }) as unknown as FeaManifestField;

describe("how far the model actually moves", () => {
    test("takes the largest named axis", () => {
        assert.equal(peakDisplacement(field({X: [-9, 4], Y: [-1, 38], Z: [0, 0.1]})), 38);
    });

    test("ignores a `magnitude` that has rotations mixed into it", () => {
        // Real numbers from this project: `magnitude` is taken over all six
        // components, so it reports 55.5 where the largest translation is 38.1.
        assert.equal(
            peakDisplacement(field({magnitude: [0, 55.5], X: [-9, 9], Y: [0, 38.1], Z: [0, 0.1]})),
            38.1,
        );
    });

    test("falls back to magnitude when no axis is named", () => {
        assert.equal(peakDisplacement(field({magnitude: [0, 55.5]})), 55.5);
    });

    test("a reduction is not an axis", () => {
        // Sesam's displacement leads with ALL, a non-negative aggregate. Taking
        // it as an axis would over-state the peak and shrink the scale.
        assert.equal(peakDisplacement(field({ALL: [0, 99], X: [-2, 2], Y: [-1, 1], Z: [0, 0]})), 2);
    });

    test("rotations are radians and stay out of it", () => {
        assert.equal(peakDisplacement(field({X: [0, 1], Y: [0, 1], Z: [0, 1], RX: [-7, 7]})), 1);
    });

    test("no field, no displacement", () => {
        assert.equal(peakDisplacement(null), 0);
        assert.equal(peakDisplacement(field({})), 0);
    });
});

describe("choosing a scale the deformation can be seen at", () => {
    test("shrinks a displacement larger than the model", () => {
        // The real case: 32 m of peak displacement on a ~20 m model. At scale 1
        // the beams fly off screen.
        const s = autoWarpScale(field({magnitude: [0, 32]}), 20);
        assert.ok(s < 1, `expected a reduction, got ${s}`);
        assert.ok(32 * s < 20, "peak deformation should stay well inside the model");
    });

    test("amplifies a displacement far smaller than the model", () => {
        // The other real case: sub-millimetre on a 100 m structure.
        const s = autoWarpScale(field({magnitude: [0, 0.0005]}), 100);
        assert.ok(s > 1, `expected amplification, got ${s}`);
        assert.ok(0.0005 * s > 1, "the deformed shape should be visible");
    });

    test("lands on a readable number, not a full-precision one", () => {
        const s = autoWarpScale(field({magnitude: [0, 3.7]}), 41);
        const mantissa = s / Math.pow(10, Math.floor(Math.log10(s)));
        assert.ok([1, 2, 5, 10].includes(Number(mantissa.toFixed(6))), `got ${s}`);
    });

    test("identity when there is nothing to go on", () => {
        // A wrong guess is worse than none: the user would have to discover the
        // number they had in order to get back to it.
        assert.equal(autoWarpScale(null, 20), 1);
        assert.equal(autoWarpScale(field({magnitude: [0, 0]}), 20), 1);
        assert.equal(autoWarpScale(field({magnitude: [0, 5]}), 0), 1);
        assert.equal(autoWarpScale(field({magnitude: [0, NaN]}), 20), 1);
    });

    test("a model already at a sensible ratio is left near identity", () => {
        // 2 m on a 20 m model is 10% — exactly the target, so scale 1.
        assert.equal(autoWarpScale(field({magnitude: [0, 2]}), 20), 1);
    });
});
