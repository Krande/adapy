// Colormaps for the streaming-FEA viewer's per-vertex colour
// computation. Each ``Colormap`` maps a normalized scalar ``t``
// (clamped to [0, 1]) to an RGB triplet with each channel in
// [0, 1], writing in place to a destination Float32Array at the
// given offset so callers can avoid per-vertex allocations.
//
// Two strategies live here:
//
//   * Tabulated (viridis): a 256-stop Uint8 LUT generated once from
//     matplotlib's canonical sample positions. Right for perceptually
//     uniform colormaps where the curve is hard to express
//     analytically.
//
//   * Piecewise-linear (abaqus, jet, coolwarm, grayscale): a tiny
//     list of (t, R, G, B) stops; the helper interpolates per call.
//     Right for the classic engineering colormaps users expect from
//     Abaqus / MATLAB, where the breakpoints are part of the visual
//     identity.
//
// Adding a new colormap = drop a function here and register it in
// ``COLORMAPS`` + the visible name list. The UI populates the
// dropdown from that registry, so the file is the single source of
// truth.

export type Colormap = (t: number, out: Float32Array, offset?: number) => void;

function clamp01(t: number): number {
    if (!isFinite(t)) return 0;
    if (t < 0) return 0;
    if (t > 1) return 1;
    return t;
}

// ── viridis (LUT) ────────────────────────────────────────────────────

const VIRIDIS_R = new Uint8Array([
    68, 68, 69, 69, 70, 70, 71, 71, 72, 72, 72, 72, 73, 73, 73, 73, 73, 73, 73, 73,
    73, 73, 73, 73, 73, 72, 72, 72, 72, 71, 71, 71, 70, 70, 69, 69, 68, 68, 67, 67,
    66, 66, 65, 64, 64, 63, 62, 62, 61, 60, 60, 59, 58, 57, 57, 56, 55, 55, 54, 53,
    52, 52, 51, 50, 49, 49, 48, 47, 46, 46, 45, 44, 43, 43, 42, 41, 41, 40, 39, 39,
    38, 37, 37, 36, 35, 35, 34, 34, 33, 33, 32, 32, 31, 31, 30, 30, 30, 29, 29, 29,
    28, 28, 28, 28, 28, 27, 27, 27, 27, 27, 27, 27, 27, 27, 28, 28, 28, 28, 28, 29,
    29, 30, 30, 31, 31, 32, 32, 33, 33, 34, 35, 35, 36, 37, 38, 39, 40, 40, 41, 42,
    43, 44, 46, 47, 48, 49, 50, 52, 53, 54, 56, 57, 58, 60, 61, 63, 65, 66, 68, 70,
    71, 73, 75, 77, 79, 81, 83, 85, 87, 89, 91, 94, 96, 98, 100, 103, 105, 107, 110, 112,
    115, 117, 120, 122, 125, 127, 130, 133, 135, 138, 141, 143, 146, 149, 152, 155, 157, 160, 163, 166,
    169, 172, 175, 178, 181, 184, 187, 190, 194, 197, 200, 203, 206, 209, 212, 215, 218, 221, 225, 228,
    231, 234, 237, 240, 243, 246, 248, 251, 253, 254, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253,
    253, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253, 253,
]);
const VIRIDIS_G = new Uint8Array([
    1, 2, 4, 6, 8, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37,
    39, 41, 43, 45, 47, 49, 51, 53, 55, 57, 59, 61, 63, 65, 67, 69, 71, 73, 75, 77,
    79, 81, 83, 85, 87, 89, 91, 93, 95, 96, 98, 100, 102, 104, 106, 108, 109, 111, 113, 115,
    117, 118, 120, 122, 123, 125, 127, 129, 130, 132, 134, 135, 137, 139, 140, 142, 143, 145, 147, 148,
    150, 151, 153, 154, 156, 157, 159, 160, 162, 163, 165, 166, 168, 169, 170, 172, 173, 175, 176, 178,
    179, 180, 182, 183, 185, 186, 188, 189, 191, 192, 193, 195, 196, 198, 199, 201, 202, 204, 205, 207,
    208, 209, 211, 212, 214, 215, 217, 218, 220, 221, 223, 224, 226, 227, 228, 230, 231, 232, 234, 235,
    236, 238, 239, 240, 241, 242, 244, 245, 246, 247, 248, 249, 250, 251, 251, 252, 253, 253, 254, 254,
    254, 255, 255, 255, 255, 255, 255, 255, 255, 254, 254, 254, 253, 253, 252, 252, 251, 250, 250, 249,
    248, 247, 246, 245, 244, 243, 242, 241, 240, 239, 237, 236, 235, 233, 232, 230, 229, 227, 226, 224,
    222, 221, 219, 217, 215, 213, 211, 209, 207, 205, 203, 201, 198, 196, 194, 191, 189, 187, 184, 182,
    179, 177, 174, 171, 169, 166, 163, 161, 158, 155, 152, 149, 146, 143, 140, 137, 134, 131, 128, 124,
    121, 117, 114, 110, 106, 102, 98, 94, 89, 84, 79, 74, 69, 63, 57, 49,
]);
const VIRIDIS_B = new Uint8Array([
    84, 86, 87, 89, 91, 92, 94, 96, 98, 100, 101, 103, 104, 106, 108, 109, 111, 112, 114, 115,
    117, 118, 119, 121, 122, 123, 124, 125, 126, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 137,
    138, 139, 140, 141, 142, 142, 143, 144, 145, 145, 146, 147, 147, 148, 148, 149, 150, 150, 151, 151,
    152, 152, 153, 153, 154, 154, 155, 155, 155, 156, 156, 157, 157, 157, 158, 158, 158, 159, 159, 159,
    160, 160, 160, 161, 161, 161, 161, 162, 162, 162, 162, 163, 163, 163, 163, 163, 164, 164, 164, 164,
    164, 164, 164, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165, 165,
    165, 165, 165, 165, 165, 165, 165, 164, 164, 164, 164, 164, 164, 163, 163, 163, 163, 163, 162, 162,
    162, 162, 161, 161, 161, 160, 160, 160, 159, 158, 158, 157, 156, 156, 155, 154, 153, 152, 151, 150,
    150, 149, 147, 146, 145, 144, 142, 141, 140, 139, 137, 136, 134, 132, 131, 129, 127, 125, 123, 121,
    119, 117, 115, 113, 111, 109, 107, 105, 103, 101, 99, 96, 94, 92, 90, 88, 85, 83, 80, 77,
    75, 72, 69, 67, 64, 61, 58, 55, 52, 49, 47, 44, 42, 40, 38, 36, 34, 33, 32, 31,
    30, 30, 30, 30, 31, 32, 33, 34, 35, 37, 38, 40, 42, 43, 45, 47, 49, 51, 53, 55,
    57, 59, 60, 62, 64, 66, 67, 69, 70, 72, 73, 74, 75, 75, 76, 76,
]);

export const viridis: Colormap = (t, out, offset = 0) => {
    const tt = clamp01(t);
    const i = Math.min(255, Math.floor(tt * 256));
    out[offset + 0] = VIRIDIS_R[i] / 255;
    out[offset + 1] = VIRIDIS_G[i] / 255;
    out[offset + 2] = VIRIDIS_B[i] / 255;
};

// ── Piecewise-linear colormaps ───────────────────────────────────────
//
// Stops are sorted ascending by t. The lookup is a linear scan — fine
// for these short lists (≤6 stops); switch to binary search if we
// ever ship a 30-stop curve.

type Stop = readonly [t: number, r: number, g: number, b: number];

function piecewise(stops: readonly Stop[]): Colormap {
    return (t, out, offset = 0) => {
        const tt = clamp01(t);
        for (let i = 1; i < stops.length; i++) {
            const s1 = stops[i];
            if (tt <= s1[0]) {
                const s0 = stops[i - 1];
                const span = s1[0] - s0[0];
                const u = span > 0 ? (tt - s0[0]) / span : 0;
                out[offset + 0] = s0[1] + (s1[1] - s0[1]) * u;
                out[offset + 1] = s0[2] + (s1[2] - s0[2]) * u;
                out[offset + 2] = s0[3] + (s1[3] - s0[3]) * u;
                return;
            }
        }
        const last = stops[stops.length - 1];
        out[offset + 0] = last[1];
        out[offset + 1] = last[2];
        out[offset + 2] = last[3];
    };
}

// Abaqus "Spectrum" — the default in CAE/Viewer. Five-segment
// blue→cyan→green→yellow→red rainbow with equal-width segments.
// The visual identity engineers expect from a stress/displacement plot.
export const abaqus: Colormap = piecewise([
    [0.00, 0.0, 0.0, 1.0],
    [0.25, 0.0, 1.0, 1.0],
    [0.50, 0.0, 1.0, 0.0],
    [0.75, 1.0, 1.0, 0.0],
    [1.00, 1.0, 0.0, 0.0],
]);

// MATLAB classic jet — same family as Abaqus but with darker endpoints
// (it dips into "navy" at 0 and "blood red" at 1). Worse perceptually
// but instantly recognisable; users sometimes want the exact MATLAB
// look for cross-tool comparison screenshots.
export const jet: Colormap = piecewise([
    [0.000, 0.0, 0.0, 0.5],
    [0.125, 0.0, 0.0, 1.0],
    [0.375, 0.0, 1.0, 1.0],
    [0.625, 1.0, 1.0, 0.0],
    [0.875, 1.0, 0.0, 0.0],
    [1.000, 0.5, 0.0, 0.0],
]);

// Diverging blue→white→red. Right for *signed* data (mode shapes,
// principal stresses about zero) where the user wants to see sign at a
// glance — zero is white, magnitude is hue saturation. Pairs naturally
// with the eigen ``[-1, +1]`` analysis-kind range.
export const coolwarm: Colormap = piecewise([
    [0.00, 0.230, 0.299, 0.754],
    [0.25, 0.564, 0.667, 0.973],
    [0.50, 0.865, 0.865, 0.865],
    [0.75, 0.957, 0.595, 0.491],
    [1.00, 0.706, 0.016, 0.150],
]);

// Pure linear grayscale. Useful for prints / overlay screenshots where
// hue would clash with other channels, or for accessibility checks.
export const grayscale: Colormap = piecewise([
    [0.0, 0.0, 0.0, 0.0],
    [1.0, 1.0, 1.0, 1.0],
]);

// ── Registry ─────────────────────────────────────────────────────────

/** Stable IDs the UI + manifest reference. Keep these stable —
 *  the active selection is stored in feaAnimationStore by name. */
export const COLORMAPS: Record<string, Colormap> = {
    viridis,
    abaqus,
    jet,
    coolwarm,
    grayscale,
};

/** Display order for the dropdown. Default first. */
export const COLORMAP_NAMES: readonly string[] = [
    "viridis",
    "abaqus",
    "jet",
    "coolwarm",
    "grayscale",
] as const;

/** Lookup with a viridis fallback so a typo in the store / a manifest
 *  pointing at a colormap we haven't shipped yet doesn't render a
 *  black mesh. */
export function getColormap(name: string | null | undefined): Colormap {
    if (name && name in COLORMAPS) return COLORMAPS[name];
    return viridis;
}
