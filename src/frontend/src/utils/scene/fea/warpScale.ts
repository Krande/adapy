import type {FeaManifestField} from "@/services/viewerApi";

// Choosing a deformation scale the model can actually be seen at.
//
// A scale of 1 is only the right default when the displacement happens to be a
// readable fraction of the model. It usually is not. Two real decks from the
// same project:
//
//   * sub-millimetre displacement on a 100 m structure — the deformed shape is
//     indistinguishable from the undeformed one, and the warp looks broken;
//   * a unit-acceleration load combination whose peak displacement is 32 m on a
//     15 m model — every beam flies off screen, and the warp looks broken in the
//     other direction.
//
// Abaqus and ParaView both solve this by deriving a scale so the largest
// deformation is a fixed fraction of the model, and letting the user override
// it. This is that derivation.

/** Peak deformation as a fraction of the model's diagonal. */
const TARGET_FRACTION = 0.1;

/** Beyond these the number stops being useful and starts being noise. */
const MIN_SCALE = 1e-6;
const MAX_SCALE = 1e6;

/**
 * Largest translation magnitude the field reaches, from the manifest alone.
 *
 * Prefers the pre-computed `magnitude` range; falls back to the largest
 * absolute per-axis bound, which is an over-estimate of the true magnitude but
 * of the right order — and this only needs the order.
 */
export function peakDisplacement(field: FeaManifestField | null | undefined): number {
    if (!field) return 0;
    const ranges = field.scalar_range ?? {};
    const magnitude = ranges["magnitude"];
    if (magnitude) return Math.max(Math.abs(magnitude[0]), Math.abs(magnitude[1]));

    // Named axes only. A displacement field can lead with a reduction (Sesam's
    // `ALL`), and rotation components are radians — neither belongs in a
    // translation magnitude.
    let peak = 0;
    for (const name of ["X", "Y", "Z", "UX", "UY", "UZ", "U1", "U2", "U3"]) {
        const r = ranges[name];
        if (r) peak = Math.max(peak, Math.abs(r[0]), Math.abs(r[1]));
    }
    return peak;
}

/** Round to the nearest 1/2/5 × 10^k, so the box shows a number, not noise. */
function nice(value: number): number {
    if (!(value > 0) || !isFinite(value)) return 1;
    const exponent = Math.floor(Math.log10(value));
    const decade = Math.pow(10, exponent);
    const mantissa = value / decade;
    const rounded = mantissa < 1.5 ? 1 : mantissa < 3.5 ? 2 : mantissa < 7.5 ? 5 : 10;
    return rounded * decade;
}

/**
 * A scale that makes the peak deformation `TARGET_FRACTION` of `modelSize`.
 *
 * `modelSize` is the model's bounding-box diagonal. Returns 1 — identity, the
 * old behaviour — whenever there is nothing to go on: no field, no
 * displacement, or a degenerate model. That matters because a wrong guess here
 * is worse than no guess: the user would have to discover the number they had
 * before in order to get back to it.
 */
export function autoWarpScale(
    field: FeaManifestField | null | undefined,
    modelSize: number,
): number {
    const peak = peakDisplacement(field);
    if (!(peak > 0) || !(modelSize > 0) || !isFinite(peak) || !isFinite(modelSize)) return 1;
    const raw = (TARGET_FRACTION * modelSize) / peak;
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, nice(raw)));
}
