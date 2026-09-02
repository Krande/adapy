// Side-by-side offset math, kept store-free (and scene-free) so it is unit
// testable — the scene handler (utils/scene/handlers/side_by_side) and the
// cellbuilder store both derive the +X shift from here.

/** Minimum aisle between the two copies, in metres. Keeps tiny models visibly
 *  apart when a proportional gap would round to nothing. */
export const MIN_SIDE_BY_SIDE_GAP = 1;

/** Aisle as a fraction of the wider copy. */
export const SIDE_BY_SIDE_GAP_FRACTION = 0.15;

/** The +X translation that seats the compiled result clear of the editable
 * topology in the side-by-side view.
 *
 * WHY SPANS AND NOT WIDTHS. This used to be
 * `max(topologyWidth, resultWidth) + gap`, which is only correct when both
 * copies start at the same X. They frequently do not: a cell topology is
 * authored from the origin outward (the demo model runs X 0 → 10), while a
 * compiled GLB may be centred on the origin (-w/2 → +w/2). The clearance
 * actually required is then `topologyMax - resultMin` = 10 + w/2, and the old
 * formula supplied about 1.15·w — short by roughly a third of the model, which
 * is exactly the overlap that kept being reported.
 *
 * Working from the two spans removes the assumption entirely: put the result's
 * LEFT edge just past the topology's RIGHT edge, whatever either happens to be.
 *
 * Both values are in the shared base frame (the model translation), so the
 * caller measures the result group relative to its own position rather than in
 * world space.
 *
 * Guarantees a strictly positive shift, so a degenerate/unmeasurable result
 * (width 0 or non-finite) still separates instead of landing on top. */
export function sideBySideOffsetX(
    topologyMaxX: number,
    resultMinX: number,
    widthHint = 0,
): number {
    const tMax = Number.isFinite(topologyMaxX) ? topologyMaxX : 0;
    const rMin = Number.isFinite(resultMinX) ? resultMinX : 0;
    const w = Number.isFinite(widthHint) && widthHint > 0 ? widthHint : 0;

    const gap = Math.max(w * SIDE_BY_SIDE_GAP_FRACTION, MIN_SIDE_BY_SIDE_GAP);
    // Never negative: if the topology is empty (tMax 0) and the result already
    // starts to the right, the shift would otherwise pull it back over.
    return Math.max(tMax - rMin, 0) + gap;
}
