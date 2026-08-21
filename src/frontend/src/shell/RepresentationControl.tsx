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

const REPS: {value: RepMode; label: string; title: string}[] = [
    {value: "topology", label: "Topology", title: "The cells and equipment you are editing"},
    {value: "simulation", label: "Sim", title: "The compiled analysis model — plates and beams"},
    {value: "detail", label: "Detail", title: "The compiled detail model — joints and fabrication"},
];

export default function RepresentationControl() {
    // Subscribe so the control follows the store — including when a compile finishes, or
    // when the same state is changed from the View menu.
    const repMode = useCellBuilderStore((s) => s.repMode);
    const active = useCellBuilderStore((s) => s.active);
    const result = useCellBuilderStore((s) => s.resultSourceName);
    useCellBuilderStore((s) => s.superimpose);
    useCellBuilderStore((s) => s.sideBySide);

    if (!active) return null;

    // Nothing compiled yet: the compiled representations are still real choices, but
    // choosing one would show an empty scene. Disabled with the reason rather than hidden
    // — hiding them means the strip changes shape the moment you compile, and controls
    // that appear from nowhere are how people conclude they imagined them.
    const noResult = result ? null : "Compile first — there is no compiled model yet";

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
                    // Topology is always available — it is the thing you are editing.
                    disabled: r.value !== "topology" && noResult != null,
                    title: r.value !== "topology" && noResult ? `${r.title} — ${noResult}` : r.title,
                }))}
            />
            <ToggleButton
                size="sm"
                variant="ghost"
                pressed={superimposeOn()}
                disabled={noResult != null}
                onClick={toggleSuperimpose}
                title={
                    noResult
                        ? `Draw the compiled model on top of the topology — ${noResult}`
                        : "Draw the compiled model on top of the topology"
                }
            >
                Overlay
            </ToggleButton>
            <ToggleButton
                size="sm"
                variant="ghost"
                pressed={sideBySideOn()}
                disabled={noResult != null}
                onClick={toggleSideBySide}
                title={
                    noResult ? `Offset the two so they sit side by side — ${noResult}` : "Offset the two so they sit side by side"
                }
            >
                Side by side
            </ToggleButton>
        </span>
    );
}
