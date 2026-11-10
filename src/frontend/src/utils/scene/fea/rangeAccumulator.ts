// The range of the values a colour scale is measured over. Plain numbers: used by the measure over
// what the three.js viewport draws (shownRange.ts) and by the one over a result layer's nodes.

/** A running min/max; null until a finite value arrives. */
export class RangeAccumulator {
    private lo = Infinity;
    private hi = -Infinity;

    add(value: number): void {
        if (!Number.isFinite(value)) return;
        if (value < this.lo) this.lo = value;
        if (value > this.hi) this.hi = value;
    }

    /** The range, widened to a non-empty one around a constant field so the colour
     *  sample stays defined; null when nothing was added. */
    result(): [number, number] | null {
        if (this.lo > this.hi) return null;
        if (this.lo === this.hi) {
            const pad = Math.abs(this.lo) > 0 ? Math.abs(this.lo) * 1e-6 : 1e-12;
            return [this.lo - pad, this.hi + pad];
        }
        return [this.lo, this.hi];
    }
}
