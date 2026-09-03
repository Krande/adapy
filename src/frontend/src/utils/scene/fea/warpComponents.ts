import type {FeaManifestField} from "@/services/viewerApi";

// Which slots in a warp field's per-point record hold the translation.
//
// The warp paths used to read the first three components of the displacement
// field as (dx, dy, dz). That held for as long as every displacement field was
// a plain vector whose components START with the translation — `["X","Y","Z"]`
// or `["X","Y","Z","RX","RY","RZ"]`.
//
// Sesam's Xtract-style displacement is not. It is
//
//     ["ALL", "X", "Y", "Z", "RX", "RY", "RZ"]
//
// where `ALL` is a REDUCTION over the record, not an axis — a positive
// magnitude-like aggregate. Reading the first three slots there warps by
// (ALL, X, Y): every vertex pushed along +x by an aggregate that is never
// negative, with the real X and Y rotated into the wrong axes. On a shell
// surface that reads as a strange bulge; on beam solids, whose vertices are
// driven entirely by their two end nodes, the beams visibly fly off.
//
// So: find the axes by NAME, and fall back to the positional reading only for a
// field that does not name them.

/** Accepted spellings per axis, lowercased. Order is x, y, z. */
const AXIS_ALIASES: readonly (readonly string[])[] = [
    ["x", "ux", "dx", "u1", "d1", "tx"],
    ["y", "uy", "dy", "u2", "d2", "ty"],
    ["z", "uz", "dz", "u3", "d3", "tz"],
];

/**
 * Offsets of (dx, dy, dz) within one point's record, or -1 for an axis the
 * field does not carry (a 1D or 2D field — the caller writes 0 there).
 *
 * Named components win. A field that names none of them keeps the historical
 * positional reading, which is what every pre-Xtract manifest relies on.
 */
export function translationOffsets(field: FeaManifestField): [number, number, number] {
    const names = field.components.map((c) => c.toLowerCase());
    const byName = AXIS_ALIASES.map((aliases) => names.findIndex((n) => aliases.includes(n)));
    // All or nothing: a field that names only some of its axes is not one whose
    // naming can be trusted to mean what this assumes, and mixing the two
    // readings would be worse than either.
    if (byName.every((i) => i >= 0)) return byName as [number, number, number];

    const n = field.components.length;
    return [n >= 1 ? 0 : -1, n >= 2 ? 1 : -1, n >= 3 ? 2 : -1];
}

/** One axis of one point, with a missing axis reading as zero. */
export function warpValue(values: Float32Array, base: number, offset: number): number {
    if (offset < 0) return 0;
    return values[base + offset] || 0;
}
