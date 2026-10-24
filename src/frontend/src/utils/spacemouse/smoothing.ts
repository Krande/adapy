// Adaptive low-pass for the raw 3D-mouse axes: the "One Euro" filter (Casiez,
// Roussel, Vogel, CHI 2012), one per axis.
//
// WHY. The cap is force-to-velocity: a steady push is a steady camera speed, so
// any wobble in the push is a wobble in speed. A gentle glide sits just past
// the dead zone, where the speed is (push - dead zone): the hand's own tremor
// (8-12 Hz) and the sensor's noise are a fixed few counts, which there is a
// large share of the signal, and reads as jitter. A mouse is position-
// controlled and shows none of this. A fixed low-pass strong enough to remove
// it would make every start and stop lag; this one smooths hard while the push
// is steady and opens up as the push changes, so starts and stops stay quick.
//
// Runs on the raw axes, before the dead zone, so the dead-zone edge sees the
// clean signal. Allocation-free: the state is caller-owned typed arrays.

export interface OneEuroParams {
    /** Cutoff in Hz while the push is steady: lower is smoother, laggier. */
    minCutoff: number;
    /** Cutoff increase per unit/s of change (axes normalised to full scale). */
    beta: number;
    /** Cutoff for the change estimate itself, Hz. */
    dCutoff: number;
}

export const DEFAULT_ONE_EURO: Readonly<OneEuroParams> = {minCutoff: 1.0, beta: 4.0, dCutoff: 1.0};

/** Filter state; starts (and resets) at rest, where every gesture begins. */
export interface OneEuroState {
    x: Float32Array;
    dx: Float32Array;
}

export function createOneEuro(n: number): OneEuroState {
    return {x: new Float32Array(n), dx: new Float32Array(n)};
}

export function resetOneEuro(s: OneEuroState): void {
    s.x.fill(0);
    s.dx.fill(0);
}

function alpha(cutoffHz: number, dt: number): number {
    const tau = 1 / (2 * Math.PI * cutoffHz);
    return 1 / (1 + tau / dt);
}

/**
 * One step of `dt` seconds: filter `input` (in units of `scale`) into `out`
 * (same units).
 */
export function stepOneEuro(
    s: OneEuroState,
    input: ArrayLike<number>,
    dt: number,
    scale: number,
    out: Float32Array,
    p: Readonly<OneEuroParams> = DEFAULT_ONE_EURO,
): void {
    const n = s.x.length;
    if (!(dt > 0)) {
        for (let i = 0; i < n; i++) out[i] = s.x[i];
        return;
    }
    const ad = alpha(p.dCutoff, dt);
    for (let i = 0; i < n; i++) {
        const v = input[i];
        // Rate of change, normalised to full scale per second.
        const rate = (v - s.x[i]) / (scale * dt);
        const dx = s.dx[i] + ad * (rate - s.dx[i]);
        s.dx[i] = dx;
        const a = alpha(p.minCutoff + p.beta * (dx < 0 ? -dx : dx), dt);
        const x = s.x[i] + a * (v - s.x[i]);
        s.x[i] = x;
        out[i] = x;
    }
}
