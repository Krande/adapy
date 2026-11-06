// What a pop-out window holds of the main window's scene, beyond the stores it
// shares with it (`@/layout/windowMirrorCore`): the facts mirrored panels show
// that the main window derives from its three.js state, which never crosses.
//
//   * `rangeCount`, `parentName` -- the selection's size and the parent level
//     "up" would select (the object info panel's count and its up arrow);
//   * `visibility`, `visVersion` -- each scene-tree row's visibility, by row id,
//     as a code (`ROW_VISIBILITY_CODES`, absent = shown). Changed in place, with
//     `visVersion` bumped, so a delta of one row does not copy 20 000 entries;
//   * `treeSelection` -- the scene-tree rows the main window has selected;
//   * `modelVisible`, `groupsTotal` -- the Scene panel's loaded models that are shown,
//     and how many groups the models carry (`../layout/sceneInfoMirror`).
//
// In the main window it stays empty.

import {create} from "zustand";

export interface SceneMirrorState {
    rangeCount: number;
    parentName: string | null;
    visibility: Map<string, number>;
    visVersion: number;
    treeSelection: readonly string[];
    /** Whether each loaded model is shown, by source name (absent: it has no scene group to flip). */
    modelVisible: Readonly<Record<string, boolean>>;
    /** How many groups the loaded models carry (a pop-out lists at most SCENE_INFO_GROUP_CAP). */
    groupsTotal: number;
}

export const useSceneMirrorStore = create<SceneMirrorState>()(() => ({
    rangeCount: 0,
    parentName: null,
    visibility: new Map(),
    visVersion: 0,
    treeSelection: [],
    modelVisible: {},
    groupsTotal: 0,
}));

/** Put visibility codes in (`reset`: they replace every row's). */
export function applyMirroredVisibility(changes: ReadonlyMap<string, number>, reset: boolean): void {
    useSceneMirrorStore.setState((s) => {
        const map = s.visibility;
        if (reset) map.clear();
        for (const [id, code] of changes) {
            if (code === 0) map.delete(id);
            else map.set(id, code);
        }
        return {visVersion: s.visVersion + 1};
    });
}
