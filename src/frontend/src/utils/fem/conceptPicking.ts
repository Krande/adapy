// A click on an analysis concept's glyph, on whichever viewport draws it: the controller that draws
// the glyphs says what is on screen, and each viewport's click handling asks here before it picks
// an object of the model. No three.js.
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import type {FemConceptRef} from "@/utils/fem/femSelection";
import type {ViewportApi} from "@/viewport/api/viewportApi";
import {pickConcept, type ConceptPick, type ConceptPicks} from "./conceptGlyphs";

let shown: {viewport: ViewportApi; picks: ConceptPicks; matrix: ArrayLike<number> | null} | null = null;

/** What `viewport` draws now (null: nothing), with where the glyphs' coordinates are in the world. */
export function setShownConcepts(viewport: ViewportApi, picks: ConceptPicks | null, matrix: ArrayLike<number> | null = null): void {
    if (picks) shown = {viewport, picks, matrix};
    else if (shown?.viewport === viewport) shown = null;
}

/** The concept drawn at a point of `viewport`'s view (CSS pixels from its corner), or null. */
export function conceptAt(viewport: ViewportApi, x: number, y: number): ConceptPick | null {
    if (!shown || shown.viewport !== viewport) return null;
    return pickConcept(shown.picks, (world, count, out) => viewport.projectToScreen(world, count, out), x, y, shown.matrix);
}

const point = new Float32Array(3), projected = new Float32Array(3);

/** How deep into `viewport`'s view a world point is (0 near, 1 far): what a box's depth is weighed against. */
export function depthOf(viewport: ViewportApi, world: ArrayLike<number>): number {
    point[0] = world[0], point[1] = world[1], point[2] = world[2];
    viewport.projectToScreen(point, 1, projected);
    return projected[2];
}

/** Make `ref` the selected concept (or with `additive` toggle it in the selection). `deselect`
 *  clears the viewport's own selection: the panel describes the concept now, not an object. */
export function selectClickedConcept(ref: FemConceptRef, additive: boolean, deselect: () => void): void {
    const info = useObjectInfoStore.getState();
    if (!additive) {
        deselect();
        info.setName(null);
        info.setJsonData(null);
        info.setSelectedNodeId(null);
        info.setClickCoordinate(null);
    }
    useFemConceptsStore.getState().selectConcept(ref, additive);
    if (!info.show_info_box) info.toggle();
}

/**
 * A click at a point of `viewport`'s view: selects the concept there and says true, or says false
 * for the viewport to go on and pick an object. The point glyphs are drawn over the structure, so
 * they are asked before it; an equipment box is a volume the structure is often inside or behind,
 * so it takes the click only when it is nearer than the surface under the cursor (`surface`: that
 * surface's point in the world, or null for empty space), or a deck could never be clicked through
 * the box standing on it.
 */
export async function takeConceptClick(
    viewport: ViewportApi, x: number, y: number, additive: boolean, surface: () => Promise<ArrayLike<number> | null>,
): Promise<boolean> {
    const pick = conceptAt(viewport, x, y);
    if (pick?.solid) {
        const under = await surface();
        if (under && depthOf(viewport, under) < pick.depth) return miss(additive);
    }
    if (!pick) return miss(additive);
    selectClickedConcept(pick.ref, additive, () => viewport.clearSelection());
    return true;
}

/** Anything else clicked replaces the concept selection, as it replaces any other. */
function miss(additive: boolean): false {
    if (!additive) useFemConceptsStore.getState().clearConceptSelection();
    return false;
}
