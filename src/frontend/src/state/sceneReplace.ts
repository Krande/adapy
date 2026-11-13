// A replace load empties the scene before the new model comes in (replace_model tears it down, the
// three.js-free viewport swaps its model). Rules that react to an empty scene - the camera going home
// (components/viewer/sceneHelpers/cameraHomeOnEmpty) - must not take that moment for the user
// clearing it: a compile showing its result with "replace" would otherwise reset the user's view.
// So a replace runs inside `duringSceneReplace`, and such a rule waits for its end to see whether
// the scene stayed empty. No three.js here: the three.js-free viewport's chunk imports it.

let depth = 0;
const ended = new Set<() => void>();

/** Whether a replace load is between tearing the scene down and the new model being in. */
export const sceneReplacing = (): boolean => depth > 0;

/** Run a replace load; the scene emptying meanwhile is part of the replace, not a clear. */
export async function duringSceneReplace<T>(run: () => Promise<T>): Promise<T> {
    depth++;
    try {
        return await run();
    } finally {
        depth--;
        if (depth === 0) for (const listener of [...ended]) listener();
    }
}

/** Called when the last running replace ends (done or failed); returns the unsubscribe. */
export function onSceneReplaceEnd(listener: () => void): () => void {
    ended.add(listener);
    return () => void ended.delete(listener);
}
