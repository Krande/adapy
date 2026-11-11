// What the "Set colour of selection…" and "Reset colours" commands do, on whichever viewport is on
// screen (viewport/api/objectColors.ts). No three.js here.

import {activeViewport, type ObjectRef} from "@/viewport/api/viewportApi";

/** Why a recolouring changed nothing. */
export const NOTHING_RECOLOURED =
    "Nothing changed. The objects already have this colour, or their colours are not theirs to change: " +
    "the elements of an FEA result keep the result's colours, and a model coloured by a property keeps those until that is reset.";

/** What a recolouring applies to: the selection, or every object when nothing is selected. */
export function recolourScope(): {refs: ObjectRef[] | null; text: string} {
    const viewport = activeViewport();
    const n = viewport?.selectionCount() ?? 0;
    if (!viewport || n === 0) return {refs: null, text: "Nothing is selected: the colour is given to every object."};
    return {refs: viewport.selection(), text: `The colour is given to the ${n === 1 ? "selected object" : `${n} selected objects`}.`};
}

/** Give the selection (or everything, with none) `color` ("#rrggbb"), or its own colours back
 *  when null. One undo step. Returns how many objects changed. */
export function colourSelection(color: string | null): number {
    const viewport = activeViewport();
    if (!viewport) return 0;
    return viewport.setObjectColors(recolourScope().refs, color);
}

/** Every object back in the model's own colour. One undo step. */
export function resetObjectColours(): number {
    return activeViewport()?.setObjectColors(null, null) ?? 0;
}
