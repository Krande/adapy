// Fading objects for a while, as both viewports do it: everything of some models but a few objects
// drawn as a faint, translucent ghost, so the few stay the only solid thing while what is around
// them stays readable. What a tool shows "around" the objects it is about (the Clashes tab's "fade
// rest" and "hide rest", which keep the focused joint's members solid).
//
// Not the user's hiding: what is hidden stays hidden and is not changed (the Scene tree reads no
// difference), there is no undo step, and taking the fade away (null) leaves every object as it
// was. A faded object is still drawn, but cannot be picked through it: a click goes to what is solid.
// No three.js here.

/** What to fade, as `ViewportApi.setObjectFade` takes it. */
export interface ObjectFade {
    /** By the source a model was loaded from (as `sourceOrigin` names it): the ids of that model's
     *  objects (a Scene tree row's `rangeId`) that stay as they are. Every other object of the model
     *  is faded; an empty set fades the model whole. A model not named is left as it is. */
    readonly keep: ReadonlyMap<string, ReadonlySet<string>>;
    /** How opaque a faded object is drawn, 0..1; 0 does not draw it at all. */
    readonly opacity: number;
}

export interface ViewportObjectFade {
    /** Fade the objects `fade` names, in place of what the last call faded; null fades nothing. A
     *  viewport without it fades nothing. */
    setObjectFade?(fade: ObjectFade | null): void;
}

/** The colour a faded object is drawn in (sRGB): the same grey on either viewport. */
export const FADE_COLOR = "#8a8f98";

/** `opacity` as a viewport draws it: within 0..1, and 0 for anything that is not a number. */
export function fadeOpacity(opacity: number): number {
    return Number.isFinite(opacity) ? Math.max(0, Math.min(1, opacity)) : 0;
}
