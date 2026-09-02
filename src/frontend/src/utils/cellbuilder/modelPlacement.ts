// Where a second (third, fourth…) procedural model sits when several are shown
// at once.
//
// Kept store-free and scene-free so it is unit testable: the store asks it
// where to put a model, the renderer just applies the number.
//
// One axis, +X, deliberately. Models are authored around a shared origin, so
// stacking them on one axis keeps "which is which" answerable by position
// alone — a 2-D pack would need a legend to read.

/** A model already placed in the scene: where it starts on X, and how wide. */
export interface PlacedModel {
    /** The X offset already applied to it. */
    offsetX: number;
    /** Its X-width in model space (0 for an empty model). */
    width: number;
}

/** Gap between neighbours, as a fraction of the incoming model's X-width.
 *
 * Proportional rather than absolute so the aisle reads the same whether the
 * models are 2 m lockers or 200 m hulls. */
export const MODEL_GAP_FRACTION = 0.5;

/** Floor for the gap, in metres.
 *
 * A zero-width model — one with no cells yet, which is exactly what a
 * just-created model is — would otherwise be placed exactly on its neighbour's
 * edge and read as part of it. */
export const MIN_MODEL_GAP = 1;

/** X-width of a set of cells, matching the store's own measure. */
export function cellsWidthX(
    cells: Iterable<{origin: readonly number[]; size: readonly number[]}>,
): number {
    let minX = Infinity;
    let maxX = -Infinity;
    for (const c of cells) {
        minX = Math.min(minX, c.origin[0]);
        maxX = Math.max(maxX, c.origin[0] + c.size[0]);
    }
    return Number.isFinite(minX) && maxX > minX ? maxX - minX : 0;
}

/** The +X offset for a model being added beside the ones already shown.
 *
 * Placed past the far edge of everything already placed, with a gap of
 * ``MODEL_GAP_FRACTION`` of the incoming model's width (floored, so a
 * zero-width model still clears its neighbour).
 *
 * Returns 0 for the first model: the first thing shown belongs at the origin,
 * where every model is authored, so a single model is never mysteriously
 * off-centre. */
export function nextModelOffsetX(placed: readonly PlacedModel[], incomingWidth: number): number {
    if (placed.length === 0) return 0;

    let farEdge = 0;
    for (const p of placed) {
        const edge = p.offsetX + (Number.isFinite(p.width) && p.width > 0 ? p.width : 0);
        if (edge > farEdge) farEdge = edge;
    }

    const w = Number.isFinite(incomingWidth) && incomingWidth > 0 ? incomingWidth : 0;
    const gap = Math.max(w * MODEL_GAP_FRACTION, MIN_MODEL_GAP);
    return farEdge + gap;
}
