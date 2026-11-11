// The colour the FEA model is drawn in where nothing paints it: a plugin (a UI shell's mode) can
// give the plain model a colour of its own while no result or property colours it, and leave every
// painted state alone.
//
// It is the model's own base colour, not an overlay, so whatever paints the model (a result, a
// property, a plugin's colour field) still draws over it exactly as before: a paint installs
// per-vertex colours and the base colour stops showing; when the paint goes, the base colour is
// the one asked for. The model's own colour is kept and given back exactly once no owner asks any
// more.
//
// Who asks for what is kept here, for both viewports. The three.js viewport writes it to the
// result mesh's material each frame (./threeUnpaintedColor.ts); a result drawn through result
// layers listens and gives it to its layers. No three.js in this module.

import type {SceneColor} from "@/plugins/registry";
import {requestRender} from "@/state/perfStore";
import {linearColor} from "@/utils/scene/sceneColor";

/** A colour as a viewport draws it: linear RGB in 0..1. */
export type LinearRgb = readonly [number, number, number];

/** What each owner asked for; the latest ask wins while several hold one. */
const asked = new Map<string, LinearRgb>();
let current: LinearRgb | null = null;
const listeners = new Set<() => void>();

function changed(): void {
    current = null;
    for (const color of asked.values()) current = color;
    requestRender();
    for (const listener of listeners) listener();
}

/** `color` for the unpainted FEA model until this owner asks again or passes null (or is released). */
export function setFeaUnpaintedColor(owner: string, color: SceneColor | null): void {
    asked.delete(owner);
    if (color != null) asked.set(owner, linearColor(color));
    changed();
}

/** Drop whatever `owner` asked for (its plugin's overlays are being removed). */
export function releaseFeaUnpaintedColor(owner: string): void {
    if (asked.delete(owner)) changed();
}

/** The colour asked for now; null while no owner asks. */
export function feaUnpaintedColor(): LinearRgb | null {
    return current;
}

/** Call `listener` when the colour asked for changes. Returns the unsubscribe function. */
export function onFeaUnpaintedColorChange(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
}

/** Tests only. */
export function _resetFeaUnpaintedColor(): void {
    asked.clear();
    current = null;
}
