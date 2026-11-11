// What follows the model on screen without being part of it (a label pinned to an element, an outline
// on a deformed surface) is moved once per frame, after whatever plays has moved the model and before
// the frame is drawn. Each viewport's frame loop calls `runBeforeDraw`; a feature listens with
// `onBeforeDraw`. With no listener a frame pays one comparison. No three.js in this module.

const listeners = new Set<() => void>();

/** Call `listener` before each frame a viewport draws (and before it decides whether to draw one,
 *  so a listener that changed something may ask for the frame). It runs every frame: it compares a
 *  few numbers and returns unless something moved. Returns the unsubscribe function. */
export function onBeforeDraw(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
}

/** For a viewport's frame loop. */
export function runBeforeDraw(): void {
    if (listeners.size === 0) return;
    for (const listener of listeners) listener();
}
