/**
 * The selection rectangle (a drag the camera mapping calls "select": Shift + left by default) while the
 * cell builder edits its cells selects CELLS (and equipment, openings, loft members), not the scene's
 * objects -- rightwards a window (a cell wholly inside), leftwards a crossing (a cell that touches it),
 * added to the builder's multi-select set or replacing it, as the scene's rectangle does with the scene's
 * selection.
 *
 * "Edits its cells" is the builder's layer as it is drawn: a procedural model open, its cells
 * shown, in the topology representation, and the layer itself visible. A mode that hides the
 * layer (Output, the FEM modes, Fabrication in the docked UI) or draws the cells only to compare
 * with a compiled model keeps the scene's rectangle: the builder staying open in the background
 * must not take the gesture from the model on screen.
 */

import * as THREE from "three";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {rectTakesBounds, type NdcRect, type ScreenBoxMode} from "@/utils/bboxSelection/screenRect";

/** Presses the builder has taken for a gizmo of its own (a translate / rotate handle): no selection
 *  rectangle starts behind them. Marked, not stopped: the gizmo listens on the same element, and a press
 *  stopped in the capture pass there never reaches its listener. */
const claimed = new WeakSet<Event>();

/** The builder takes this press (`setupPointerHandler` then draws no rectangle for it). */
export function claimPress(ev: Event): void {
    claimed.add(ev);
}

/** Has the builder taken this press. */
export function pressClaimed(ev: Event): boolean {
    return claimed.has(ev);
}

/** The name of the builder's layer in the scene (sceneContext's container). */
export const BUILDER_LAYER = "__cellbuilder__";

/** Whether `object` and every parent of it are visible: whether it is drawn. */
export function shownInScene(object: THREE.Object3D | null): boolean {
    for (let o = object; o; o = o.parent) if (!o.visible) return false;
    return true;
}

/** The builder's layer when a Shift+drag there selects cells; null when it selects the scene's objects. */
export function builderLayerEditing(scene: THREE.Object3D): THREE.Object3D | null {
    const st = useCellBuilderStore.getState();
    if (st.active === null || !st.cellsVisible || st.repMode !== "topology") return null;
    const layer = scene.getObjectByName(BUILDER_LAYER) ?? null;
    return layer && shownInScene(layer) ? layer : null;
}

/** The world box of every object drawn under `layer` tagged `key` (`__cellId` or `__refId`), by that
 *  tag's value, as [minX, minY, minZ, maxX, maxY, maxZ]. An object hidden on its own (the per-cell/
 *  per-ref eye, the include view's "included only") is not drawn, so it is left out. */
function drawnBoundsByKey(layer: THREE.Object3D, key: "__cellId" | "__refId"): Map<string, number[]> {
    layer.updateWorldMatrix(true, true);
    const out = new Map<string, number[]>();
    const box = new THREE.Box3();
    layer.traverse((o) => {
        const id = o.userData[key];
        if (typeof id !== "string" || !(o as THREE.Mesh).isMesh || !shownInScene(o)) return;
        box.setFromObject(o);
        if (box.isEmpty()) return;
        const b = out.get(id);
        if (!b) {
            out.set(id, [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z]);
            return;
        }
        b[0] = Math.min(b[0], box.min.x);
        b[1] = Math.min(b[1], box.min.y);
        b[2] = Math.min(b[2], box.min.z);
        b[3] = Math.max(b[3], box.max.x);
        b[4] = Math.max(b[4], box.max.y);
        b[5] = Math.max(b[5], box.max.z);
    });
    return out;
}

/** The world box of each cell drawn under `layer`, by cell id, as [minX, minY, minZ, maxX, maxY, maxZ].
 *  A cell hidden on its own (the per-cell eye, the include view's "included only") is not drawn. */
export function drawnCellBounds(layer: THREE.Object3D): Map<string, number[]> {
    return drawnBoundsByKey(layer, "__cellId");
}

/** The ref analogue of `drawnCellBounds`: every drawn (visible, un-hidden) plugin ref's world box,
 *  by ref id. A ref drawn `look: "none"` has no mesh at all, so it is never a candidate here. */
export function drawnRefBounds(layer: THREE.Object3D): Map<string, number[]> {
    return drawnBoundsByKey(layer, "__refId");
}

/** The ids a rectangle on the screen takes, from their world boxes (cells or refs alike -- both are
 *  boxes). `viewProj` is the camera's projection x view matrix (column-major). */
export function cellsInScreenRect(
    bounds: ReadonlyMap<string, ArrayLike<number>>,
    viewProj: ArrayLike<number>,
    rect: NdcRect,
    mode: ScreenBoxMode,
): string[] {
    const out: string[] = [];
    for (const [id, b] of bounds) if (rectTakesBounds(b, 0, viewProj, rect, mode)) out.push(id);
    return out;
}

/** The cells AND refs the rectangle takes, added to the builder's selection or, with `add` false, in
 *  place of it (`selectItems`): the primary becomes the most recently taken item, cells and refs
 *  alike, matching every other multi-select entry point (a toggle click, the Model panel). Returns
 *  how many it took in total. */
export function selectCellsInScreenRect(layer: THREE.Object3D, camera: THREE.Camera, rect: NdcRect, mode: ScreenBoxMode, add = true): number {
    camera.updateMatrixWorld();
    const viewProj = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).elements;
    const takenCells = cellsInScreenRect(drawnCellBounds(layer), viewProj, rect, mode);
    const takenRefs = cellsInScreenRect(drawnRefBounds(layer), viewProj, rect, mode);
    const taken = takenCells.length + takenRefs.length;
    if (taken || !add) {
        // "add" already joins the existing selectedCellIds/selectedRefIds internally (selectItems),
        // so only the newly taken ids are passed -- concatenating the existing ones too would just
        // make them the (deduplicated-away) start of the list and change nothing but readability.
        useCellBuilderStore.getState().selectItems([...takenCells, ...takenRefs], add ? "add" : "only");
        requestRender();
    }
    return taken;
}
