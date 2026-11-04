// FEA streaming: repainting when what is drawn changes.
//
// Owns: keeping a scale measured over the drawn elements (shownRange.ts) true to the
// drawing. Hiding, isolating or revealing elements -- a group isolation, the
// dummy-mass toggle, a manual hide -- and switching the beam solids on or off change
// which values are on screen, so the scale and the colours on it are redrawn through
// the step callback the loader registered: the blobs are cached, so this is CPU only.
//
// Inputs: `useVisibilityStore` (bumped by every draw-range hide/show), the session's
// beam-solid mesh, and what the last paint recorded with `notePaintedShown`.

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {feaSession} from "./session";

/** What was drawn when the scale was last measured, or null when it was not measured
 *  over the drawing (a pinned or envelope scale, a property). */
let paintedKey: string | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let subscribed = false;

/** Re-measures the last paint's scale over what is drawn now, and the range it was
 *  painted with. A repaint is every surface's colours and markers again; the measure
 *  is one pass over the values, and most changes of what is drawn (the beam solids
 *  for the coloured lines, a hidden plate inside the extremes) leave the range as it
 *  was. */
type ShownMeasure = () => [number, number] | null;
let pendingMeasure: ShownMeasure | null = null;
let measure: ShownMeasure | null = null;
let paintedRange: readonly [number, number] | null = null;

function drawnKey(): string {
    const solids = feaSession.active?.beamSolidMesh?.visible ?? false;
    return `${useVisibilityStore.getState().version}:${solids}`;
}

/** Called by a paint that measured its scale over what is drawn: how to measure it again. */
export function noteShownMeasure(fn: ShownMeasure): void {
    pendingMeasure = fn;
}

/** Record that a paint just measured its scale over what is drawn (or did not), and
 *  the range it painted with. */
export function notePaintedShown(rangeFromShown: boolean, range: readonly [number, number] | null = null): void {
    paintedKey = rangeFromShown ? drawnKey() : null;
    measure = rangeFromShown ? pendingMeasure : null;
    paintedRange = range;
    pendingMeasure = null;
}

function sameRange(a: readonly [number, number] | null, b: readonly [number, number] | null): boolean {
    return a === b || (!!a && !!b && a[0] === b[0] && a[1] === b[1]);
}

/** Repaint the current step if what is drawn changed since it was painted and the
 *  scale measured over it moved. Coalesced: one isolation hides and shows ranges
 *  several times in one go. */
export function scheduleShownRangeRepaint(): void {
    if (timer !== null) return;
    timer = setTimeout(() => {
        timer = null;
        if (paintedKey === null || paintedKey === drawnKey()) return;
        if (measure && sameRange(measure(), paintedRange)) {
            paintedKey = drawnKey();
            return;
        }
        const {sessionActive, applyStep, stepIndex} = useFeaAnimationStore.getState();
        if (!sessionActive || !applyStep) return;
        applyStep(stepIndex).catch((err) => {
            // eslint-disable-next-line no-console
            console.warn("[fea] repaint after a visibility change failed", err);
        });
    }, 0);
}

/** Follow draw-range visibility from now on. Idempotent. */
export function followShownRange(): void {
    if (subscribed) return;
    subscribed = true;
    useVisibilityStore.subscribe(scheduleShownRangeRepaint);
}
