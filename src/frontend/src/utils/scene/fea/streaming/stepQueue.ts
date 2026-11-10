// FEA streaming: one step change at a time.
//
// Owns: the queue every step request of a shown result goes through, whichever
// viewport draws it. Holds the one pending request and the drain in flight.

// Step changes run one at a time, latest wins. Play, the slider and Stop all ask
// for steps; loads take a network round trip each, so without this an older load
// could finish after a newer one and repaint its frame (Stop during play landed
// back on the in-flight frame, and a second press was needed). Requests arriving
// while a load runs collapse into "the newest one"; the returned promise settles
// once the queue is drained, i.e. when the LAST requested step is on screen.
let pendingStep: {step: number; run: (step: number) => Promise<void>} | null = null;
let stepDrain: Promise<void> | null = null;

export function requestStep(step: number, run: (step: number) => Promise<void>): Promise<void> {
    pendingStep = {step, run};
    if (!stepDrain) {
        stepDrain = (async () => {
            try {
                while (pendingStep) {
                    const next = pendingStep;
                    pendingStep = null;
                    try {
                        await next.run(next.step);
                    } catch (err) {
                        // A superseded load failing is not the caller's error: carry on
                        // to the newer step. Only the last one's failure surfaces.
                        if (!pendingStep) throw err;
                    }
                }
            } finally {
                stepDrain = null;
            }
        })();
    }
    return stepDrain;
}
