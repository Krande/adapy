/**
 * Selection boxes against the scene: a box made around the current selection, the
 * selection a box makes, and the selection a rectangle drawn on the screen makes.
 *
 * Which objects a box or a rectangle takes, and how large the selection is, are asked of the
 * viewport on screen (viewport/api/boxSelection.ts), so each of these works on the three.js viewport
 * and on the three.js-free one. No three.js here.
 */

import {recordBoxAction, useBBoxSelectionStore} from "@/state/bboxSelectionStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {activeViewport} from "@/viewport/api/viewportApi";

import {padded, type Aabb, type Vec3} from "./boxMath";
import type {NdcRect, ScreenBoxMode} from "./screenRect";

/** The world AABB of the current selection, or null when nothing selected has geometry. */
export function selectionBounds(): Aabb | null {
    return activeViewport()?.selectionBounds() ?? null;
}

/** A new selection box around the current selection, a little larger so what it was made
 *  from is fully inside. Returns its id, or null when nothing selected has geometry. */
export function addBoxAroundSelection(): string | null {
    const b = selectionBounds();
    if (!b) return null;
    const diag = Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]);
    // The box and the selection it then makes are one undo step.
    return recordBoxAction("box-add", () => {
        const id = useBBoxSelectionStore.getState().add(padded(b, Math.max(0.05, diag * 0.02)));
        applyBoxSelection(id);
        return id;
    });
}

/** Replace the selection with what box `id` takes. Returns how many elements that is. */
export function applyBoxSelection(id: string): number {
    const box = useBBoxSelectionStore.getState().boxes.find((b) => b.id === id);
    const viewport = activeViewport();
    if (!box || !viewport) return 0;
    const taken = viewport.objectsInBox({min: box.min, max: box.max}, box.mode);
    // Dragging a box's handles re-applies it on every move: the moves of one drag coalesce into
    // one undo entry.
    viewport.setSelection(taken, {
        coalesce: `box:${id}`,
        within: () => {
            const info = useObjectInfoStore.getState();
            info.setName(`${box.label}: ${taken.size} element${taken.size === 1 ? "" : "s"}`);
            info.setSelectedNodeId(null);
        },
    });
    requestRender();
    return taken.size;
}

/** Add what a rectangle drawn on the screen takes (`screenBox`) to the selection. Returns how
 *  many elements it took. */
export function selectInScreenRect(rect: NdcRect, mode: ScreenBoxMode): number {
    const viewport = activeViewport();
    if (!viewport) return 0;
    const taken = viewport.objectsInScreenRect(rect, mode);
    if (taken.size) {
        // The whole drag is one undo entry (Ctrl+Z), however many elements it took.
        viewport.setSelection(taken, {
            add: true,
            within: () => {
                const info = useObjectInfoStore.getState();
                info.setName(`${mode === "window" ? "Window" : "Crossing"} selection: ${taken.size} element${taken.size === 1 ? "" : "s"}`);
                info.setSelectedNodeId(null);
            },
        });
    }
    requestRender();
    return taken.size;
}

const sameVec = (a: Vec3, b: Vec3): boolean => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];

/** Keep the selection the active box's: whenever that box's bounds or mode change (a handle was
 *  dragged, the mode switched, an undo put it back), what it takes now is selected. Each viewport's
 *  box controller binds this while it is up. Returns the function that stops it. */
export function bindActiveBoxSelection(): () => void {
    return useBBoxSelectionStore.subscribe((s, prev) => {
        const now = s.boxes.find((b) => b.id === s.activeId);
        const before = prev.boxes.find((b) => b.id === s.activeId);
        if (now && before && (now.mode !== before.mode || !sameVec(now.min, before.min) || !sameVec(now.max, before.max))) {
            applyBoxSelection(now.id);
        }
    });
}
