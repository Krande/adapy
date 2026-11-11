// Recolouring the model's objects, as both viewports do it: a colour per object, given by the user
// in place of the model's own and taken back with null.
//
// A colour is the object's base colour: it is lit like the rest of the model, hiding, isolating
// and section planes keep it, and the selection's highlight is drawn over it. The elements of an
// FEA result are never recoloured: a result's colours are the result's, painted or not (the plain
// colour of an unpainted result is `setFeaUnpaintedColor`). A call that names only such elements
// changes nothing and returns 0.
//
// Each call is one undo step: the history (utils/scene/viewerHistory.ts) keeps the colours before
// and after it as a state only the viewport that made it reads. No three.js here.

import type {StateSlice} from "@/utils/scene/viewerHistory";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import {activeViewport, type ObjectRef} from "./viewportApi";

/** The colours the user gave objects at one moment. Opaque outside the viewport that made it. */
export interface ObjectColorState {
    /** How many objects have a colour in it. */
    readonly count: number;
}

export interface ViewportObjectColors {
    /** Give `refs` (every object on screen when null) the colour `color` (sRGB, "#rrggbb"), or
     *  the model's own colour back when it is null. One undo step. As with `setSelection`, `refs`
     *  is read once and no ref is kept. Returns how many objects changed colour. */
    setObjectColors(refs: Iterable<ObjectRef> | null, color: string | null): number;
    /** How many objects have a colour given to them. */
    objectColorCount(): number;
    /** The given colours as they are now; the same object until one changes. */
    captureObjectColors(): ObjectColorState;
    /** Go back to a state this viewport captured; a state of another viewport is ignored. */
    restoreObjectColors(state: ObjectColorState): void;
}

const HEX = /^#[0-9a-f]{6}$/i;

/** "#rrggbb" as 0xRRGGBB, or null when the text is not that. */
export function parseHexColor(color: string): number | null {
    return HEX.test(color) ? parseInt(color.slice(1), 16) : null;
}

/** The given colours of the viewport on screen as a slice of the history: undo and redo put them
 *  back on whichever viewport is on screen then. */
export const objectColorsSlice: StateSlice<ObjectColorState | null> = {
    get: () => activeViewport()?.captureObjectColors() ?? null,
    set: (state) => {
        if (state) activeViewport()?.restoreObjectColors(state);
    },
};

/** Run `change` (a recolouring of the viewport on screen) as one undo step. */
export function recordObjectColors<T>(reset: boolean, change: () => T): T {
    return recordViewerAction(reset ? "color-reset" : "color-set", change, {slices: [objectColorsSlice as StateSlice]});
}
