// How a model PROPERTY is coloured: its own scheme, not the result colormap.
//
// A property field (category "property": plate thickness, material, beam
// section) says what an element IS. It goes through the result pipeline - one
// element-average value per element - but it is not a result, and painting it
// with the result colormap got two things wrong:
//
//   * A categorical property stores the deck's own ids ("S355" is 1, a section
//     is 37). Sampling a continuous ramp at those ids gave neighbouring ids
//     neighbouring colours, so two materials could be told apart only by
//     squinting, and which two depended on how the deck happened to number them.
//     Categories get a qualitative palette instead: one distinct colour per id,
//     assigned by rank, so the colours are as far apart as the palette allows.
//
//   * The user's result colormap, contour bands and pinned range are settings for
//     reading results. Banding a list of materials, or clipping a thickness range
//     to a stress limit typed in for another field, describes nothing. A property
//     ignores all three: categories by palette, numeric properties (thickness)
//     on the field's own sequential map over the field's own range.
//
// Pure: no three, no store, so it runs under `node --test`. The painter and the
// legend both call it, which is what keeps a swatch the colour its elements are.

import type {FeaManifestField} from "@/services/viewerApi";
import {getColormap, type Colormap} from "./colormaps";

/** The fields this module colours. Everything else keeps the result colormap. */
export function isPropertyField(field: Pick<FeaManifestField, "category"> | null | undefined): boolean {
    return field?.category === "property";
}

// A qualitative palette: Tableau 10 followed by its lighter companions, so the
// first ten categories are the strongest and most distinct. Beyond twenty the
// hues continue around the colour wheel at the golden angle, which keeps any
// two consecutive ranks far apart however many there are.
const QUALITATIVE: readonly (readonly [number, number, number])[] = [
    [0x4e, 0x79, 0xa7],
    [0xf2, 0x8e, 0x2b],
    [0xe1, 0x57, 0x59],
    [0x76, 0xb7, 0xb2],
    [0x59, 0xa1, 0x4f],
    [0xed, 0xc9, 0x48],
    [0xb0, 0x7a, 0xa1],
    [0xff, 0x9d, 0xa7],
    [0x9c, 0x75, 0x5f],
    [0xba, 0xb0, 0xac],
    [0xa0, 0xcb, 0xe8],
    [0xff, 0xbe, 0x7d],
    [0xff, 0x9d, 0x9a],
    [0x86, 0xbc, 0xb6],
    [0x8c, 0xd1, 0x7d],
    [0xf1, 0xce, 0x63],
    [0xd4, 0xa6, 0xc8],
    [0xfa, 0xbf, 0xd2],
    [0xd7, 0xb5, 0xa6],
    [0x79, 0x70, 0x6e],
].map(([r, g, b]) => [r / 255, g / 255, b / 255] as const);

const GOLDEN_ANGLE = 0.381966011250105;

function hslToRgb(h: number, s: number, l: number, out: Float32Array, offset: number): void {
    const a = s * Math.min(l, 1 - l);
    const channel = (n: number) => {
        const k = (n + h * 12) % 12;
        return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    };
    out[offset] = channel(0);
    out[offset + 1] = channel(8);
    out[offset + 2] = channel(4);
}

/** The colour of the category at `rank` (0-based), written in place. */
export function categoryColor(rank: number, out: Float32Array, offset = 0): void {
    const i = Number.isFinite(rank) && rank > 0 ? Math.floor(rank) : 0;
    if (i < QUALITATIVE.length) {
        const [r, g, b] = QUALITATIVE[i];
        out[offset] = r;
        out[offset + 1] = g;
        out[offset + 2] = b;
        return;
    }
    const k = i - QUALITATIVE.length;
    const hue = (0.07 + k * GOLDEN_ANGLE) % 1;
    // Alternate the lightness too, so two hues that land close together on the
    // wheel still differ in brightness.
    const light = k % 3 === 0 ? 0.45 : k % 3 === 1 ? 0.6 : 0.72;
    hslToRgb(hue, 0.62, light, out, offset);
}

/** A categorical property's stored codes, ascending; empty for a numeric one. */
export function categoryCodes(
    field: Pick<FeaManifestField, "value_labels"> | null | undefined,
): number[] {
    const labels = field?.value_labels;
    if (!labels) return [];
    const codes = Object.keys(labels)
        .map(Number)
        .filter((v) => Number.isFinite(v));
    codes.sort((a, b) => a - b);
    return codes;
}

/** Index of the code nearest `value` in an ascending list. */
function nearestIndex(codes: readonly number[], value: number): number {
    let lo = 0;
    let hi = codes.length - 1;
    while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (codes[mid] < value) lo = mid + 1;
        else hi = mid;
    }
    if (lo > 0 && Math.abs(codes[lo - 1] - value) <= Math.abs(codes[lo] - value)) return lo - 1;
    return lo;
}

/** The sequential map a numeric property is drawn on: the one its bake names. */
export function propertySequentialColormap(
    field: Pick<FeaManifestField, "default_view"> | null | undefined,
): Colormap {
    return getColormap(field?.default_view?.colormap ?? "viridis");
}

/**
 * Most distinct values a numeric property is coloured by rank and listed one by
 * one in its legend; past this it is coloured linearly by value under a gradient
 * legend. One number for both, so the legend always describes the painting.
 * Thickness on a real deck is a handful of plate sizes.
 */
export const MAX_RANKED_VALUES = 32;

/** The distinct finite values in `values`, ascending. */
export function distinctValues(values: Iterable<number>): number[] {
    const seen = new Set<number>();
    for (const v of values) if (Number.isFinite(v)) seen.add(v);
    return Array.from(seen).sort((a, b) => a - b);
}

/**
 * The colormap a property field is painted with over `range`, or null for a
 * field that is not a property (paint it with the result colormap).
 *
 * The painters hand a colormap a normalised `t`, not the value, so the value is
 * recovered from `t` and snapped to the nearest known one:
 *
 *   * a category snaps to its stored code - deck ids, whole numbers at least one
 *     apart, so the float error of the round trip cannot reach the next one - and
 *     takes that code's palette colour;
 *   * a numeric property (thickness) given the distinct values it carries,
 *     `levels`, is coloured by RANK along its sequential map. A plate thickness is
 *     a handful of sizes, and one 200 mm insert spread over a linear scale
 *     squeezed every 8 to 25 mm plate - nearly the whole deck - into one end of
 *     it, the same colour. By rank each size gets its own step. Without levels,
 *     or with more than `MAX_RANKED_VALUES` of them, it is linear in the value.
 */
export function propertyColormap(
    field: Pick<FeaManifestField, "category" | "value_labels" | "default_view"> | null | undefined,
    range: readonly [number, number],
    levels?: readonly number[] | null,
): Colormap | null {
    if (!isPropertyField(field)) return null;
    const [lo, hi] = range;
    const span = hi - lo;
    const valueAt = (t: number) => lo + (Number.isFinite(t) ? t : 0) * span;
    const codes = categoryCodes(field);
    if (codes.length > 0) {
        return (t, out, offset = 0) => categoryColor(nearestIndex(codes, valueAt(t)), out, offset);
    }
    const map = propertySequentialColormap(field);
    if (!levels || levels.length === 0 || levels.length > MAX_RANKED_VALUES) return map;
    const sorted = [...levels].sort((a, b) => a - b);
    const last = sorted.length - 1;
    return (t, out, offset = 0) => {
        const rank = nearestIndex(sorted, valueAt(t));
        map(last > 0 ? rank / last : 0.5, out, offset);
    };
}

function css(out: Float32Array): string {
    const c = (i: number) => Math.round(Math.min(1, Math.max(0, out[i])) * 255);
    return `rgb(${c(0)}, ${c(1)}, ${c(2)})`;
}

/** One legend entry of a property: a value, what it is called, its colour. */
export interface PropertyLegendEntry {
    value: number;
    label: string;
    /** ``rgb(r, g, b)``, ready for a style attribute. */
    color: string;
}

function formatNumber(value: number): string {
    return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(6)));
}

/** Most values a numeric property's legend lists; see `MAX_RANKED_VALUES`. */
export const MAX_LISTED_VALUES = MAX_RANKED_VALUES;

/**
 * The legend of a property field: one entry per value, coloured exactly as the
 * painter colours it, restricted to `present` when that is known. `levels` is
 * every distinct value the painter coloured by (hidden elements included), which
 * is what a numeric property's colours are ranked over - so hiding a plate size
 * does not shift the colours of the others in the legend while the model keeps
 * them.
 *
 * Categories are listed by name; a numeric property by value, with its unit,
 * ascending - unless it has more distinct values than `MAX_LISTED_VALUES`, when
 * this returns null and the legend draws the sequential gradient instead.
 */
export function propertyLegendEntries(
    field: Pick<FeaManifestField, "category" | "value_labels" | "default_view" | "unit"> | null | undefined,
    range: readonly [number, number],
    present?: ReadonlySet<number> | null,
    levels?: readonly number[] | null,
): PropertyLegendEntry[] | null {
    if (!isPropertyField(field)) return null;
    const map = propertyColormap(field, range, levels ?? (present ? distinctValues(present) : null));
    if (!map) return null;
    const rgb = new Float32Array(3);
    const [lo, hi] = range;
    const span = hi - lo;
    const tOf = (value: number) => (span > 0 ? (value - lo) / span : 0);

    const labels = field?.value_labels;
    const codes = categoryCodes(field);
    if (codes.length > 0 && labels) {
        const out: PropertyLegendEntry[] = [];
        for (const code of codes) {
            if (present && !present.has(code)) continue;
            map(tOf(code), rgb);
            out.push({value: code, label: labels[String(code)] ?? formatNumber(code), color: css(rgb)});
        }
        out.sort((a, b) => a.label.localeCompare(b.label, undefined, {numeric: true}));
        return out;
    }

    // Numeric: only the values actually painted can be listed; without them the
    // gradient is the honest picture.
    if (!present || present.size === 0) return null;
    const ranked = levels ?? distinctValues(present);
    if (ranked.length > MAX_LISTED_VALUES) return null;
    const unit = field?.unit ? ` ${field.unit}` : "";
    return Array.from(present)
        .filter((v) => Number.isFinite(v))
        .sort((a, b) => a - b)
        .map((value) => {
            map(tOf(value), rgb);
            return {value, label: `${formatNumber(value)}${unit}`, color: css(rgb)};
        });
}
