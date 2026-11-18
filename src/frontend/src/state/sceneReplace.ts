// A replace load empties the scene before the new model comes in (replace_model tears it down, the
// three.js-free viewport swaps its model). Rules that react to an empty scene - the camera going home
// (components/viewer/sceneHelpers/cameraHomeOnEmpty) - must not take that moment for the user
// clearing it: a compile showing its result with "replace" would otherwise reset the user's view.
// So a replace runs inside `duringSceneReplace`, and such a rule waits for its end to see whether
// the scene stayed empty. No three.js here: the three.js-free viewport's chunk imports it.

import {create} from "zustand";

interface SceneReplaceState {
    /** How many replace loads are running (they may overlap). */
    running: number;
    beginReplace(): void;
    endReplace(): void;
}

const useSceneReplaceStore = create<SceneReplaceState>((set, get) => ({
    running: 0,
    beginReplace: () => set({running: get().running + 1}),
    endReplace: () => set({running: Math.max(0, get().running - 1)}),
}));

/** Whether a replace load is between tearing the scene down and the new model being in. */
export const sceneReplacing = (): boolean => useSceneReplaceStore.getState().running > 0;

/** Run a replace load; the scene emptying meanwhile is part of the replace, not a clear. */
export async function duringSceneReplace<T>(run: () => Promise<T>): Promise<T> {
    const {beginReplace, endReplace} = useSceneReplaceStore.getState();
    beginReplace();
    try {
        return await run();
    } finally {
        endReplace();
    }
}

/** Called when the last running replace ends (done or failed); returns the unsubscribe. */
export function onSceneReplaceEnd(listener: () => void): () => void {
    return useSceneReplaceStore.subscribe((now, before) => {
        if (before.running > 0 && now.running === 0) listener();
    });
}
