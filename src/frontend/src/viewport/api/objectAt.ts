// Objects a viewport draws in a way its own pick does not see (a beam drawn as a line, where the
// renderer picks triangles): whoever draws them says which one is at a point of the view, and the
// viewport asks after its own pick. No three.js.
import type {ObjectRef, ViewportApi} from "./viewportApi";

/** The object drawn at a point of `viewport`'s view (CSS pixels from its corner), or null. `surface`
 *  is the world point the viewport's own pick found there (null for empty space): the object is
 *  given only when it is in front of that. */
export type ObjectAt = (viewport: ViewportApi, x: number, y: number, surface: ArrayLike<number> | null) => ObjectRef | null;

const finders = new Set<ObjectAt>();

/** Register a finder. Returns the function that withdraws it. */
export function addObjectAt(finder: ObjectAt): () => void {
    finders.add(finder);
    return () => void finders.delete(finder);
}

/** The first object a finder gives for the point, or null. */
export function objectAt(viewport: ViewportApi, x: number, y: number, surface: ArrayLike<number> | null): ObjectRef | null {
    for (const finder of finders) {
        const ref = finder(viewport, x, y, surface);
        if (ref) return ref;
    }
    return null;
}
