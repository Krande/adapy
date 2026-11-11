// Box selection, as a viewport answers it. The boxes themselves are plain data (state/bboxSelectionStore)
// and so are the rules (utils/bboxSelection/boxMath: "partially within" and "fully within" a box,
// utils/bboxSelection/screenBox: a window and a crossing rectangle on screen). What only the viewport
// knows is geometric: which of its objects a box or a rectangle takes, and how large the selection is.
// A feature asks the viewport on screen (ViewportApi has these members) and selects what comes back,
// so the same command works on both. No three.js here.
import type {Aabb, BoxSelectMode} from "@/utils/bboxSelection/boxMath";
import type {NdcRect, ScreenBoxMode} from "@/utils/bboxSelection/screenRect";
import type {ObjectRef} from "./viewportApi";

/** Objects a viewport found, e.g. the ones inside a box. Iterating gives each as an `ObjectRef`; one
 *  ref is yielded again and again with new values (read it and move on, keep none), so a set of tens
 *  of thousands of objects makes no garbage per object. Handed back to the viewport it came from
 *  (`setSelection`), it is taken as it is, without a lookup per object. */
export interface ObjectSet extends Iterable<ObjectRef> {
    /** How many objects it holds. */
    readonly size: number;
}

export const NO_OBJECTS: ObjectSet = {size: 0, [Symbol.iterator]: () => [][Symbol.iterator]()};

export interface ViewportBoxSelection {
    /** The objects a world-space box takes: in "full" mode the ones wholly inside it, in "partial"
     *  mode the ones that touch it. Only what is drawn counts: an object that is hidden, or that a
     *  section plane cuts away whole, is never in the answer. How exactly "touches" is decided is the
     *  viewport's: on its triangles where it has them, else on its box. */
    objectsInBox(box: Aabb, mode: BoxSelectMode): ObjectSet;
    /** The objects a rectangle on the screen takes (normalised device coordinates, as `ndcRect`
     *  makes them): a "window" the ones wholly inside it, a "crossing" the ones that touch it. Only
     *  what is drawn counts, and nothing behind the camera. */
    objectsInScreenRect(rect: NdcRect, mode: ScreenBoxMode): ObjectSet;
    /** The world-space box around the selected objects; null when none of them draws anything. */
    selectionBounds(): Aabb | null;
}
