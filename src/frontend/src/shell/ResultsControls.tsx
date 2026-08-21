import React, {Suspense, lazy} from "react";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";

const AnimationControls = lazy(() =>
    import("@/components/simulation/SimulationControls").then((m) => ({default: m.AnimationControls})),
);

// The result field and its display knobs, in the Results toolbar itself.
//
// These were the Simulation panel, then a popover hung off a gear button. The popover was
// the wrong answer twice over: it hid the current field and step — the two things you most
// want to READ while looking at a result — behind a click, and it put the controls
// somewhere you had to remember rather than somewhere you could see.
//
// They are simply in the strip now. Everything the panel had is here: field, component,
// step, deformation scale, period, warp factor, colormap, layer, IP reduction, smoothing
// and the warp toggle. The strip already scrolls horizontally, which is what makes that
// affordable — a control you have to scroll to is still better than one you have to know
// about.
//
// Nothing is hidden when it does not apply. Layer and IP reduction are element-field
// concepts and simply do not render for a nodal field (that is the component's own rule,
// and correct — they are not "disabled", they are meaningless). What DOES grey out is the
// whole group when no result is loaded, which is the honest state: the controls exist,
// they have nothing to act on yet.

export default function ResultsControls() {
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);

    return (
        <span
            className={
                "inline-flex min-w-0 items-center " +
                // Greyed rather than absent: a strip that changes shape when a result
                // loads gives you nothing to aim at beforehand, and the gap where the
                // controls will be is itself information.
                (sessionActive ? "" : "pointer-events-none opacity-40")
            }
            aria-disabled={!sessionActive}
            title={sessionActive ? undefined : "Load a result set to use these"}
        >
            <Suspense fallback={null}>
                <AnimationControls inline />
            </Suspense>
        </span>
    );
}
