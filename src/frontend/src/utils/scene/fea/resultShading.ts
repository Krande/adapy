// How a shaded FEA result is lit, as numbers both renderers draw by. No three.js in this module.
//
// A shaded result colour is the legend's colour darkened by how its face is turned, and nothing
// else: in linear light,
//
//     shown = legend * min(1, exposure * (ambient + headlight * max(N.L, 0)) / PI)
//
// one factor for all three channels, so the hue and the saturation are the legend's and only the
// value drops. No highlight: a specular term adds the same amount to every channel, which greys a
// saturated colour (a pure green's blue and red channels come up off zero) and reads as a paler
// colour than the legend's. And no channel is clipped: at a bright setting the factor passes 1, and
// clipping each channel at 1 brings the weaker channels up towards the strongest (green towards mint,
// cyan towards pale teal). `exposure` scales the light down so a face square to the headlight is at
// most the legend colour, never brighter; the faces turned away keep their shading below it.
//
// The ambient and headlight values are the appearance settings' (`lightIntensities`), the same lights
// the rest of the model is lit by.

import {lightIntensities, type AppearanceSettings} from "@/state/appearanceStore";

/** The factor the lights are scaled by for a shaded result: 1 while a face square to the headlight
 *  is lit to at most its legend colour, less when the lights would light it brighter. */
export function resultExposure(s?: Pick<AppearanceSettings, "brightness" | "ambientShare">): number {
    const {ambient, headlight} = lightIntensities(s);
    const brightest = (ambient + headlight) / Math.PI;
    return brightest > 1 ? 1 / brightest : 1;
}

/** The factor a shaded result colour (linear) is multiplied by, for a face whose normal makes
 *  `nDotL` with the headlight. In 0..1. */
export function resultShadeFactor(nDotL: number, s?: Pick<AppearanceSettings, "brightness" | "ambientShare">): number {
    const {ambient, headlight} = lightIntensities(s);
    const lit = (ambient + headlight * Math.max(0, nDotL)) / Math.PI;
    return Math.min(1, lit * resultExposure(s));
}

/** A linear colour shaded as above. */
export function shadeResultColor(
    rgb: readonly [number, number, number], nDotL: number, s?: Pick<AppearanceSettings, "brightness" | "ambientShare">,
): [number, number, number] {
    const k = resultShadeFactor(nDotL, s);
    return [rgb[0] * k, rgb[1] * k, rgb[2] * k];
}
