/**
 * FREE MOVE: the left Shift key held while a gizmo is dragged.
 *
 * Owns: whether the left Shift key is down now, and telling those who ask when that changes. While it
 * is down a drag of a builder gizmo (a space, an equipment, an opening, a plugin's box) does not snap:
 * not to the grid step, and not to a neighbour's vertex. Let go, and the drag snaps again from where
 * the pointer is. Only the left Shift: the right one stays free for the keys that read Shift (Shift+H
 * and the like are not pressed during a drag, but a typist's right hand is left alone).
 *
 * The camera's Shift (a profile's Shift+drag, e.g. GeniE's Shift+right button zoom) is read when the
 * camera takes a press; a press on a gizmo handle is the gizmo's, and the camera is off for the drag.
 *
 * No three.js in this module: the three.js-free viewport's side loads it.
 */

let held = false;
const listeners = new Set<() => void>();

/** Whether a drag moves freely now (the left Shift key is down). */
export function freeMove(): boolean {
    return held;
}

/** Set whether the left Shift key is down (the key listener, and tests). */
export function setFreeMove(on: boolean): void {
    if (on === held) return;
    held = on;
    for (const listener of listeners) listener();
}

/** Call `listener` when free move starts or ends. Returns the unsubscribe function. */
export function onFreeMoveChange(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
}

/** The step a drag snaps to now: `step`, or none (0) while it moves freely. */
export function snapStep(step: number): number {
    return held ? 0 : step;
}

/** What the keys are heard on: the window (or a stand-in for one). */
export interface FreeMoveKeySource {
    addEventListener(type: string, listener: (e: {code?: string}) => void, capture: boolean): void;
    removeEventListener(type: string, listener: (e: {code?: string}) => void, capture: boolean): void;
}

let installs = 0;
let uninstall: (() => void) | null = null;

/** Follow the left Shift key on `target` (the window). Counted: the last release stops it. */
export function installFreeMoveKeys(target: FreeMoveKeySource = window as unknown as FreeMoveKeySource): () => void {
    if (installs++ === 0) {
        const down = (e: {code?: string}) => void (e.code === "ShiftLeft" && setFreeMove(true));
        const up = (e: {code?: string}) => void (e.code === "ShiftLeft" && setFreeMove(false));
        // A key let go outside the window (or with the tab hidden) is never heard: start over then.
        const reset = () => setFreeMove(false);
        // The capture phase, before a handler that stops the key (the builder's own, on capture too).
        target.addEventListener("keydown", down, true);
        target.addEventListener("keyup", up, true);
        target.addEventListener("blur", reset, true);
        uninstall = () => {
            target.removeEventListener("keydown", down, true);
            target.removeEventListener("keyup", up, true);
            target.removeEventListener("blur", reset, true);
            setFreeMove(false);
        };
    }
    let released = false;
    return () => {
        if (released) return;
        released = true;
        if (--installs === 0) {
            uninstall?.();
            uninstall = null;
        }
    };
}
