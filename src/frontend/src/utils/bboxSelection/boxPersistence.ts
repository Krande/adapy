/**
 * Keeps the selection boxes of the current scope in this browser, so they survive a reload.
 *
 * Restoring puts the boxes back and applies no selection: the models they would select may not
 * be loaded yet. A box's "Reselect" applies it once they are.
 *
 * Boxes follow the scene's recentring offset: when it changes (a scene rebuilt with another model
 * loaded first, or shown by the other viewport, which places models differently), they move by the
 * difference and stay over the same geometry. The offset is read as plain numbers
 * (state/sceneFrameStore), so this binding serves the three.js viewport and the three.js-free one
 * alike. No three.js here.
 */

import {useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

import type {Vec3} from "./boxMath";
import {readStoredBoxes, shiftedBoxes, writeStoredBoxes} from "./boxStorage";

let bound = false;

const currentScope = () => scopeUrlPart(useScopeStore.getState().current);

function currentOffset(): Vec3 | null {
    const t = useSceneFrameStore.getState().offset;
    return t ? [t[0], t[1], t[2]] : null;
}

/** Restore the current scope's boxes and keep them stored from now on. Returns the unbinding;
 *  binding twice is a no-op. */
export function bindBBoxSelectionStorage(): () => void {
    if (bound) return () => {};
    bound = true;
    const boxes = useBBoxSelectionStore;
    let scope = currentScope();
    /** The recentring offset the boxes in the store are expressed in. */
    let offset: Vec3 | null = null;

    const load = () => {
        const stored = readStoredBoxes(scope);
        offset = stored.offset;
        boxes.getState().restore(stored.boxes, stored.activeId);
    };
    const save = () => {
        const s = boxes.getState();
        offset ??= currentOffset();
        writeStoredBoxes(scope, {offset, boxes: s.boxes, activeId: s.activeId});
    };

    load();

    const unsubBoxes = boxes.subscribe((s, prev) => {
        if (s.boxes !== prev.boxes || s.activeId !== prev.activeId) save();
    });

    const unsubScope = useScopeStore.subscribe((s) => {
        const next = scopeUrlPart(s.current);
        if (next === scope) return;
        scope = next;
        load();
    });

    const unsubModel = useSceneFrameStore.subscribe((s, prev) => {
        if (s.offset === prev.offset || !s.offset) return;
        const t: Vec3 = [s.offset[0], s.offset[1], s.offset[2]];
        const st = boxes.getState();
        const from = offset;
        offset = t;
        if (!st.boxes.length) return;
        const d: Vec3 = from ? [t[0] - from[0], t[1] - from[1], t[2] - from[2]] : [0, 0, 0];
        if (d.every((v) => v === 0)) {
            save();
            return;
        }
        // In two steps, so the controller sees no change to the active box's bounds and
        // applies no selection.
        const active = st.activeId;
        boxes.setState({boxes: shiftedBoxes(st.boxes, d), activeId: null});
        boxes.setState({activeId: active});
    });

    return () => {
        unsubBoxes();
        unsubScope();
        unsubModel();
        bound = false;
    };
}
