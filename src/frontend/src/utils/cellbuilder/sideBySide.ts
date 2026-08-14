// Side-by-side offset math, kept store-free (and scene-free) so it is unit
// testable — the scene handler (utils/scene/handlers/side_by_side) and the
// cellbuilder store both derive the +X shift from here.

/** The +X translation that seats the compiled result clear of the editable
 * topology in the side-by-side view.
 *
 * Both copies share one centering frame (the model translation) and span the
 * same model-space X range, so shifting the result by
 * `max(topologyWidth, resultWidth) + gap` starts it past the topology's far
 * edge with an aisle between them — regardless of which copy is wider, and
 * regardless of whether the result group was measurable at call time (it falls
 * back to the topology width, which the store always knows from the cells).
 *
 * Guarantees a strictly positive shift (never 0/NaN), so the two never coincide
 * even for a degenerate/empty result whose measured width is 0 or non-finite —
 * the case that made a personal-scope demo render the result on top of the
 * topology instead of beside it. */
export function sideBySideOffsetX(
  topologyWidthX: number,
  resultWidthX: number,
): number {
  const wt =
    Number.isFinite(topologyWidthX) && topologyWidthX > 0 ? topologyWidthX : 0;
  const wr =
    Number.isFinite(resultWidthX) && resultWidthX > 0 ? resultWidthX : 0;
  const width = Math.max(wt, wr);
  // 15% of the wider span as an aisle, floored at 1 m so tiny models still
  // separate visibly. width + gap >= max(wt,wr) + margin >= (wt+wr)/2 + margin,
  // which is the clearance the two same-centred copies need to not overlap.
  const gap = Math.max(width * 0.15, 1);
  return width + gap;
}
