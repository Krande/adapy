// How a result value becomes a colour: the range it is measured against, and
// whether the ramp is continuous or cut into discrete contour bands.
//
// Every FE post-processor offers this and for the same reason. A continuous ramp
// answers "where is it worst"; banded contours answer "which elements are over
// 250 MPa", and that is the question an engineer actually has. Reading a value
// off a smooth gradient is guesswork — reading which band an element is in is
// not. A fixed range matters for the same reason: two load cases coloured on
// their own extremes cannot be compared, and the first thing anyone does when
// comparing is pin the scale.
//
// Pure functions over numbers. No store, no three.js, no React — the colouring
// kernels, the legend and the settings dialog all derive what they need from
// here, which is what keeps the swatch in the legend and the colour on the
// element the same colour.

import {getColormap, type Colormap} from "./colormaps";

/** The display settings a result's colour scale is drawn under. */
export interface ContourSettings {
    /**
     * Number of discrete bands, or null for a continuous ramp.
     *
     * A band is a closed interval of the range that gets ONE colour, sampled at
     * the band's midpoint so no band ever takes an endpoint colour that another
     * band shares.
     */
    levels: number | null;
    /** Manual lower bound, or null to use the field's own minimum. */
    min: number | null;
    /** Manual upper bound, or null to use the field's own maximum. */
    max: number | null;
}

export const DEFAULT_CONTOUR: ContourSettings = {levels: null, min: null, max: null};

/** Bands the UI offers, and the bounds a typed value is held to. */
export const MIN_LEVELS = 2;
export const MAX_LEVELS = 32;

/**
 * The range to colour over: the field's own, with either end overridden.
 *
 * Each end independently, because pinning only the top is a real request — "show
 * me everything above yield in red, let the bottom fall where it falls".
 *
 * An inverted or empty result is rejected in favour of the automatic range. A
 * user mid-way through typing "-1e8" has momentarily asked for min > max, and a
 * viewport that goes black while they type is worse than one that waits.
 */
export function resolveContourRange(
    auto: readonly [number, number],
    settings?: ContourSettings | null,
): [number, number] {
    if (!settings) return [auto[0], auto[1]];
    const lo = settings.min !== null && Number.isFinite(settings.min) ? settings.min : auto[0];
    const hi = settings.max !== null && Number.isFinite(settings.max) ? settings.max : auto[1];
    if (!(hi > lo)) return [auto[0], auto[1]];
    return [lo, hi];
}

/** A whole number of bands within the offered bounds, or null for continuous. */
export function normaliseLevels(levels: number | null | undefined): number | null {
    if (levels === null || levels === undefined || !Number.isFinite(levels)) return null;
    const n = Math.round(levels);
    if (n < MIN_LEVELS) return MIN_LEVELS;
    if (n > MAX_LEVELS) return MAX_LEVELS;
    return n;
}

/**
 * The colormap to sample with, banded when levels are set.
 *
 * Wrapping rather than changing the maps themselves: banding is a property of
 * how a scale is DRAWN, not of the colours, so every colormap can be banded and
 * a colormap needs to know nothing about it.
 */
export function bandedColormap(map: Colormap, levels: number | null): Colormap {
    const n = normaliseLevels(levels);
    if (n === null) return map;
    return (t, out, offset = 0) => {
        const clamped = t < 0 ? 0 : t > 1 ? 1 : Number.isFinite(t) ? t : 0;
        // The last band is closed at the top: without this, exactly 1.0 falls
        // into a band that does not exist and the maximum element goes black.
        const band = Math.min(n - 1, Math.floor(clamped * n));
        map((band + 0.5) / n, out, offset);
    };
}

/** The colormap for a name and a band count, in one call. */
export function contourColormap(name: string | null | undefined, levels: number | null): Colormap {
    return bandedColormap(getColormap(name), levels);
}

/** One band of the legend: the values it covers and the colour it is drawn in. */
export interface ContourBand {
    /** Lower bound of the band, in field units. */
    from: number;
    /** Upper bound. */
    to: number;
    /** ``rgb(r, g, b)``, ready for a style attribute. */
    color: string;
}

function cssColor(map: Colormap, t: number): string {
    const rgb = new Float32Array(3);
    map(t, rgb);
    const c = (i: number) => Math.round(rgb[i] * 255);
    return `rgb(${c(0)}, ${c(1)}, ${c(2)})`;
}

/**
 * The legend's bands, bottom-up, for a range and a band count.
 *
 * Bottom-up because that is the order they are stacked in; the legend renders
 * the array reversed. Derived from the same midpoint rule ``bandedColormap``
 * paints with, so a swatch is exactly the colour its elements are.
 */
export function contourBands(
    range: readonly [number, number],
    levels: number,
    colormapName: string | null | undefined,
): ContourBand[] {
    const n = normaliseLevels(levels) ?? MIN_LEVELS;
    const map = getColormap(colormapName);
    const [lo, hi] = range;
    const width = (hi - lo) / n;
    const bands: ContourBand[] = [];
    for (let band = 0; band < n; band++) {
        bands.push({
            from: lo + band * width,
            to: lo + (band + 1) * width,
            color: cssColor(map, (band + 0.5) / n),
        });
    }
    return bands;
}

/** One named value of a categorical field, and the colour it is painted in. */
export interface CategoryEntry {
    /** The code the field stores for this category. */
    value: number;
    /** What the deck calls it — "S355", a section name. */
    label: string;
    /** ``rgb(r, g, b)``, ready for a style attribute. */
    color: string;
}

/**
 * The named values of a categorical field, in label order, each with its colour.
 *
 * Material, plate thickness and beam section are not measurements — they are a
 * handful of named things, and a gradient from 1 to 7 says nothing about which
 * element is which. What a reader needs is the deck's own names against the
 * colours on screen, so this returns exactly that, sampled from the same map at
 * the same position the painter uses.
 *
 * ``present`` restricts the list to codes that are actually on screen. A legend
 * naming twelve materials for a section cut that shows two is a legend describing
 * a model the reader is not looking at.
 */
export function categoryEntries(
    valueLabels: Readonly<Record<string, string>> | undefined,
    range: readonly [number, number],
    colormapName: string | null | undefined,
    present?: ReadonlySet<number> | null,
): CategoryEntry[] {
    if (!valueLabels) return [];
    const map = getColormap(colormapName);
    const [lo, hi] = range;
    const span = hi - lo;
    const out: CategoryEntry[] = [];
    for (const [rawValue, label] of Object.entries(valueLabels)) {
        const value = Number(rawValue);
        if (!Number.isFinite(value)) continue;
        if (present && !present.has(value)) continue;
        out.push({value, label, color: cssColor(map, span > 0 ? (value - lo) / span : 0.5)});
    }
    out.sort((a, b) => a.label.localeCompare(b.label, undefined, {numeric: true}));
    return out;
}

/**
 * Evenly spaced tick VALUES for a continuous scale, top down.
 *
 * The legend drew these itself from a "step" that meant something else; sharing
 * them here is what lets a banded legend and a continuous one be the same
 * component with the same numbers in the same places.
 */
export function contourTicks(range: readonly [number, number], count: number): number[] {
    const ticks = Math.max(1, Math.round(count));
    const [lo, hi] = range;
    return Array.from({length: ticks + 1}, (_, i) => hi - (i * (hi - lo)) / ticks);
}
