// Banded colour scales: explicit value intervals, each with its own colour.
//
// Core's contour bands split a range evenly and colour each band off a colormap. A
// utilisation check, a code-check pass/fail or a criterion with fixed thresholds wants
// something else: bands whose edges and colours are part of the scheme. A plugin states
// the scale once, colours its elements with `scaleColor`, and core's legend draws the
// same bands (SceneHandle.showColorScale) -- so a swatch is exactly what is painted.
//
// Dependency-free (no DOM, store or three), so it sits on the root `@/viewer-core` entry.

/** sRGB, as CSS: "#ffaa00", or [r, g, b] in 0..1. */
export type ScaleColor = string | readonly [number, number, number];

export interface ColorScaleBand {
    /** Lower edge, inclusive. The lowest band also takes everything below it. */
    from: number;
    color: ScaleColor;
    /** Shown instead of the lower edge in the legend, e.g. "> 1.0 (fails)". */
    label?: string;
}

export interface ColorScale {
    /** Ascending by `from`. The top band takes everything above its edge. */
    bands: readonly ColorScaleBand[];
    /** Legend header lines, field first (e.g. ["Utilisation", "UF [-]"]). */
    title?: readonly string[];
    /** Upper edge of the top band, shown at the top of the legend; omit for open-ended. */
    max?: number;
}

/** The colour of `value` on `scale`: the band whose interval holds it. */
export function scaleColor(scale: ColorScale, value: number): ScaleColor {
    const bands = scale.bands;
    let hit = bands[0];
    for (const band of bands) if (value >= band.from) hit = band;
    return hit.color;
}

/** `color` as a CSS colour string. */
export function cssScaleColor(color: ScaleColor): string {
    if (typeof color === "string") return color;
    const c = (i: number) => Math.round(Math.min(1, Math.max(0, color[i])) * 255);
    return `rgb(${c(0)}, ${c(1)}, ${c(2)})`;
}
