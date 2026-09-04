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

// A CLAMP, not a normalisation. Scaling every model so its peak deformation is
// a fixed fraction of its size sounds tidy and is not: on a deck where 39 mast
// nodes reach 32 m while the other 2,880 average 0.32 m, it divides everything
// by the outlier and the bulk of the model stops visibly deforming at all --
// which reads as the warp having broken. A quantile instead of a maximum only
// moves the arbitrariness into the choice of quantile.
//
// So: leave the scale alone unless the deformation is unusable, and say what
// unusable means. Too big to keep on screen, or too small to see.

/** Above this multiple of the model, bring the peak back down to it. */
const TOO_LARGE = 0.5;
/** Below this multiple of the model, bring the peak up to VISIBLE. */
const TOO_SMALL = 0.005;
/** Where an invisibly small deformation is brought to. */
const VISIBLE = 0.05;

/** Beyond these the number stops being useful and starts being noise. */
const MIN_SCALE = 1e-6;
const MAX_SCALE = 1e6;

/**
 * Largest translation the field reaches, from the manifest alone.
 *
 * NAMED AXES FIRST, not the pre-computed `magnitude`. A six-component
 * displacement field's `magnitude` is taken over every component, rotations
 * included — on a real deck here it reports 55.5 where the largest translation
 * is 38.1, because radians got summed with metres. `magnitude` is still the
 * fallback for a field that names no axes, where an over-estimate of the right
 * order beats nothing.
 *
 * Taking the largest single axis rather than a true vector magnitude is a
 * deliberate over-estimate: the peaks may be at different nodes. It is a bound
 * on translation, and a scale only needs the order.
 */
export function peakDisplacement(field: FeaManifestField | null | undefined): number {
    if (!field) return 0;
    const ranges = field.scalar_range ?? {};

    // A displacement field can also lead with a REDUCTION (Sesam's `ALL`), which
    // is not an axis either — hence an explicit list rather than "every component".
    let peak = 0;
    for (const name of ["X", "Y", "Z", "UX", "UY", "UZ", "U1", "U2", "U3"]) {
        const r = ranges[name];
        if (r) peak = Math.max(peak, Math.abs(r[0]), Math.abs(r[1]));
    }
    if (peak > 0) return peak;

    const magnitude = ranges["magnitude"];
    return magnitude ? Math.max(Math.abs(magnitude[0]), Math.abs(magnitude[1])) : 0;
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
 * A scale that keeps the deformation usable, or 1 when it already is.
 *
 * `modelSize` is the model's bounding-box diagonal. Returns 1 -- identity, the
 * previous behaviour -- for anything in the wide middle, and for any input
 * there is nothing to go on in. That matters because a wrong guess here is
 * worse than no guess: the user would have to discover the number they had in
 * order to get back to it.
 */
export function autoWarpScale(
    field: FeaManifestField | null | undefined,
    modelSize: number,
): number {
    const peak = peakDisplacement(field);
    if (!(peak > 0) || !(modelSize > 0) || !isFinite(peak) || !isFinite(modelSize)) return 1;

    if (peak > TOO_LARGE * modelSize) {
        return clamp(nice((TOO_LARGE * modelSize) / peak));
    }
    if (peak < TOO_SMALL * modelSize) {
        return clamp(nice((VISIBLE * modelSize) / peak));
    }
    return 1;
}

const clamp = (s: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));

