// Box selection on the three.js-free viewport: which objects a selection box or a rectangle on the
// screen takes, and the box around the selection (viewport/api/boxSelection.ts).
//
// The scene keeps no triangles on this side, only each object's box as the GLB reader reported it
// (the object with everything below it). So an object is "fully within" a box when its box is, which
// is exact, and "partially within" when its box touches it, which is coarser than the three.js
// viewport's answer: that one looks at the triangles of an object on the boundary, and leaves out a
// slanted or L-shaped one whose box reaches in while the object itself does not. The same goes for a
// rectangle on the screen (utils/bboxSelection/screenRect.ts, `rectTakesBounds`).
import {useSectionStore} from "@/state/sectionStore";
import {containedAt, overlapsAt, type Aabb, type BoxSelectMode} from "@/utils/bboxSelection/boxMath";
import {rectTakesBounds, type NdcRect, type ScreenBoxMode} from "@/utils/bboxSelection/screenRect";
import {boxCutAway, type PlaneLike} from "@/utils/mesh_select/rangeVisibility";
import type {ObjectSet} from "@/viewport/api/boxSelection";
import type {ObjectRef} from "@/viewport/api/viewportApi";
import {splitObjectKey, type NativeScene} from "./nativeScene";

/** Objects by the scene's own keys: the three.js-free viewport's `ObjectSet`. Its `setSelection`
 *  takes the keys as they are; anything else reads them as object refs. */
export class KeySet implements ObjectSet {
    constructor(readonly keys: readonly string[]) {}

    get size(): number {
        return this.keys.length;
    }

    *[Symbol.iterator](): Generator<ObjectRef> {
        const ref: ObjectRef = {model: "", id: ""};
        for (const key of this.keys) {
            const {model, id} = splitObjectKey(key);
            ref.model = model;
            ref.id = id;
            yield ref;
        }
    }
}

/** The enabled section planes, as the test for "cut away whole" takes them; null for none. */
function cuttingPlanes(): PlaneLike[] | null {
    const planes = useSectionStore.getState().planes.filter((p) => p.enabled);
    return planes.length ? planes.map((p) => ({normal: {x: p.normal[0], y: p.normal[1], z: p.normal[2]}, constant: p.constant})) : null;
}

export function nativeObjectsInBox(scene: NativeScene, box: Aabb, mode: BoxSelectMode): KeySet {
    const planes = cuttingPlanes();
    const takes = mode === "full" ? containedAt : overlapsAt;
    return new KeySet(scene.drawnObjectsWhere((bounds, i) => takes(bounds, i, box), planes ? (bounds, o) => boxCutAway(bounds, o, planes) : undefined));
}

export function nativeObjectsInScreenRect(scene: NativeScene, rect: NdcRect, mode: ScreenBoxMode): KeySet {
    const planes = cuttingPlanes();
    const m = scene.viewProjection();
    return new KeySet(scene.drawnObjectsWhere((bounds, i) => rectTakesBounds(bounds, i * 6, m, rect, mode), planes ? (bounds, o) => boxCutAway(bounds, o, planes) : undefined));
}

export function nativeSelectionBounds(scene: NativeScene): Aabb | null {
    const b = scene.selectionBounds();
    return b ? {min: [b[0], b[1], b[2]], max: [b[3], b[4], b[5]]} : null;
}
