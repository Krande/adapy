// Where the models' own coordinates are in the world, as plain numbers: world = model + offset. The
// three.js viewport recentres what it loads (state/modelState's `translation`, mirrored here); the
// three.js-free one draws a model where its file puts it, so its offset is zero. What is kept in world
// coordinates across page loads (the selection boxes) or sent to a source in the model's own frame
// (a box's request) reads the offset from here, whichever viewport is on screen. No three.js.
import {create} from "zustand";

export type SceneOffset = readonly [number, number, number];

interface SceneFrameState {
    /** The offset in use; null while nothing has set one (no model placed yet). */
    offset: SceneOffset | null;
}

export const useSceneFrameStore = create<SceneFrameState>(() => ({offset: null}));

/** Publish the offset the viewport on screen places models by. Each call is a change to whoever
 *  subscribes, the same numbers included: a scene rebuilt in the same place still says so. */
export function setSceneOffset(offset: SceneOffset | null): void {
    useSceneFrameStore.setState({offset: offset ? [offset[0], offset[1], offset[2]] : null});
}
