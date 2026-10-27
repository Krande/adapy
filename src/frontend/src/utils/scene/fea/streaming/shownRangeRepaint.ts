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

function drawnKey(): string {
    const solids = feaSession.active?.beamSolidMesh?.visible ?? false;
    return `${useVisibilityStore.getState().version}:${solids}`;
}

/** Record that a paint just measured its scale over what is drawn (or did not). */
export function notePaintedShown(rangeFromShown: boolean): void {
    paintedKey = rangeFromShown ? drawnKey() : null;
}

/** Repaint the current step if what is drawn changed since it was painted. Coalesced:
 *  one isolation hides and shows ranges several times in one go. */
export function scheduleShownRangeRepaint(): void {
    if (timer !== null) return;
    timer = setTimeout(() => {
        timer = null;
        if (paintedKey === null || paintedKey === drawnKey()) return;
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
