// Core taking the scene's clicks for a while: every click, wherever it lands, before either viewport
// works out what was clicked.
//
// A plugin's pick (plugins/scenePicks.ts) is handed a click on an OBJECT, after the viewport found
// it. A core tool that points at places rather than objects -- the Measure tab, which snaps to the
// corner or edge nearest the pointer and looks at the surface under it itself -- needs the click as
// a position in the view, empty space included, and needs it before anything is selected: a click
// it takes selects nothing, clears nothing and is no undo step. Drags are not clicks: the camera
// still orbits and pans while a tool holds them.
//
// Dependency-free: both viewports' click handlers and the tools share it. No three.js.

/** A click in the view: CSS pixels from the view's top-left corner, as `projectToScreen` counts. */
export interface SceneClick {
    x: number;
    y: number;
    /** The click carried the key that adds to a selection (shift, ctrl, cmd). */
    additive: boolean;
}

/** The clicks being held; `end` gives them back. */
export interface SceneClickSession {
    end: () => void;
}

interface Holder {
    owner: string;
    onClick: (click: SceneClick) => boolean | void;
}

const holders: Holder[] = [];

/** Take the scene's clicks until `end()`. `onClick` returning false lets a click through to whoever
 *  held them before, then to the viewport. */
export function takeSceneClicks(owner: string, onClick: Holder["onClick"]): SceneClickSession {
    const holder: Holder = {owner, onClick};
    holders.push(holder);
    return {
        end: () => {
            const at = holders.indexOf(holder);
            if (at >= 0) holders.splice(at, 1);
        },
    };
}

/** Whether anybody holds the clicks: a viewport asks before it picks. */
export function sceneClicksHeld(): boolean {
    return holders.length > 0;
}

/** Hand a click to whoever holds them, newest first. True when it was taken: the viewport then picks
 *  and selects nothing. A holder that throws loses the clicks rather than every click after it. */
export function offerSceneClick(click: SceneClick): boolean {
    for (let i = holders.length - 1; i >= 0; i--) {
        const holder = holders[i];
        try {
            if (holder.onClick(click) !== false) return true;
        } catch (err) {
            holders.splice(holders.indexOf(holder), 1);
            // eslint-disable-next-line no-console
            console.error(`[viewport] ${holder.owner}: its scene click handler threw and was ended`, err);
        }
    }
    return false;
}
