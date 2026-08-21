import React from "react";
import {SegmentedControl, ToggleButton} from "@/components/ui";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {
    setRepresentation,
    sideBySideOn,
    superimposeOn,
    toggleSideBySide,
    toggleSuperimpose,
    type RepMode,
} from "./buildActions";

// What you are looking at while you build: the topology you are editing, the compiled
// result, or both.
//
// These lived in the Builder panel's View tab. Dissolving that tab, I sent them to
// View ▸ Builder as menu commands — wrong for these particular controls. You reach for
// them after every compile, to see what the compiler made of the model. A menu is where
// occasional commands go; something used once per edit loop belongs on the strip, visible,
// showing its state without being opened first.
//
// The representation is a SegmentedControl because the three are mutually exclusive and
// the control says so — you can see which one you are in without hovering anything.
// Superimpose and side-by-side are independent modifiers on top of it, so they stay
// separate toggles.
//
// They carry TEXT, not icons. There is no icon in the set that means "superimpose" or
// "side by side", and the near-misses (two overlapping rectangles, which is the copy
// glyph) would say something else confidently. These are the two controls that went
// missing when the View tab dissolved; being findable beats being compact.
//
// Deliberately NOT tied to the mode. "Build shows topology, Inspect shows the compiled
// model" is the obvious shortcut and it breaks the non-modality contract in modeStore: a
// mode changes what is OFFERED, never what is loaded or visible. It would also mean
// leaving the Build tools to compare two representations — at exactly the moment you want
// them. Buttons, not modes.

// The tooltips say what each one IS, in the compiler's own terms.
//
// "Sim" and "Detail" are the same build at two levels of detail, and the difference is not
// guessable from the labels — worth spelling out, including the case where there is no
// difference at all: with no structural blueprint the detail flag has no effect, so Detail
// renders the same geometry as Sim. Someone comparing them and seeing nothing change
// deserves to know that is the model, not the button.
const REPS: {value: RepMode; label: string; title: string}[] = [
    {
        value: "topology",
        label: "Topology",
        title: "The cells, openings and equipment you are editing — the input to a compile",
    },
    {
        value: "simulation",
        label: "Sim",
        title: "Compiled, analysis grade: plates and beams as the analysis wants them",
    },
    {
        value: "detail",
        label: "Detail",
        title:
            "Compiled, fabrication grade: deck plate edges trimmed to the girder flanges, " +
            "I-girder joints modelled, connection joints added. Identical to Sim when the " +
            "model has no structural blueprint — the detail flag has nothing to act on.",
    },
];

export default function RepresentationControl() {
    // Subscribe so the control follows the store — including when a compile finishes, or
    // when the same state is changed from the View menu.
    const repMode = useCellBuilderStore((s) => s.repMode);
    const active = useCellBuilderStore((s) => s.active);
    useCellBuilderStore((s) => s.superimpose);
    useCellBuilderStore((s) => s.sideBySide);

    if (!active) return null;

    // Nothing is disabled on "no result loaded", and the first version of this control got
    // that wrong badly enough to trap people.
    //
    // Switching to Topology UNLOADS the compiled model and nulls resultSourceName — that is
    // what Topology means. Gating Sim and Detail on "a result is currently loaded" therefore
    // greyed them out the instant you looked at your topology, with no way back short of
    // compiling again. The signal was wrong: "is a result on screen" is not "can I ask for
    // one".
    //
    // `setRepMode` already compiles a preview when the representation you pick has no GLB
    // yet. Asking for Sim IS the way to get Sim. So the control just asks, and a compile
    // that cannot run reports itself in a toast like any other.

    return (
        <span className="inline-flex items-center gap-1">
            <SegmentedControl<RepMode>
                size="sm"
                label="Representation"
                value={repMode as RepMode}
                onChange={(v) => setRepresentation(v)()}
                options={REPS.map((r) => ({
                    value: r.value,
                    label: r.label,
                    title: r.title,
                }))}
            />
            <ToggleButton
                size="sm"
                variant="ghost"
                pressed={superimposeOn()}
                onClick={toggleSuperimpose}
                title="Draw the compiled model on top of the topology"
            >
                Overlay
            </ToggleButton>
            <ToggleButton
                size="sm"
                variant="ghost"
                pressed={sideBySideOn()}
                onClick={toggleSideBySide}
                title="Offset the two so they sit side by side"
            >
                Side by side
            </ToggleButton>
        </span>
    );
}
