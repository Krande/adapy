import React from "react";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {Icon, IconButton, cn, type IconName} from "@/components/ui";
import {useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {panelsForMode} from "./panelRegistry";
import {fitAll, focusSelection, hideSelection, unhideAll} from "./inspectActions";
import {openFemConcepts, toggleDataTable, toggleLegend} from "./resultsActions";
import {redo, undo} from "./buildActions";
import {openConvert, openUpload, refreshFiles} from "./dataActions";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {Z} from "./zIndex";

// The dynamic tool palette — Cinema 4D R25's central idea, and the direct answer to
// "too much on screen at once".
//
// Two bands:
//   * PINNED tools, identical in every mode. C4D keeps select/move/rotate/scale fixed
//     for exactly this reason: the things you reach for constantly must not move when
//     you change discipline.
// The rail holds ONLY tools that mean the same thing in every mode.
//
// It used to swap its contents per mode, following Cinema 4D's dynamic palettes. That
// was wrong for this product: C4D's modes are closely-related modelling contexts sharing
// most of their tools, while ours are four different applications, so the rail turned
// over almost completely and nothing had a fixed address. Mode-specific tools now live in
// a horizontal toolbar under the mode switcher, where changing contents is expected
// because they sit directly beneath the control that changes them.
//
// Undo and redo are the clearest case. They were in the Build rail, which said "undo is a
// modelling feature" — but undo is universal in every application anyone has used. They
// belong here, greyed when there is nothing to undo. That is the general rule now: a
// feature that is universally understood stays put and greys out; it does not vanish and
// reappear.
//
// Panel toggles are gone from the rail. The menu bar lists every panel with its shortcut,
// which is a better index than a column of unlabelled icons, and the duplication was
// costing the rail the space its actual tools need.

interface RailTool {
    id: string;
    icon: IconName;
    label: string;
    shortcut?: string;
    /** Not yet wired — rendered disabled with an honest tooltip rather than hidden, so
     *  the shape of the mode is visible during the rebuild. */
    pending?: boolean;
    /** Renders as a rule instead of a button. Keeps grouping in the data, not the JSX. */
    divider?: boolean;
    /** Delegates to an existing handler. Never reimplements behaviour. */
    run?: () => void;
    /** Returns null when usable, else why it is greyed — shown in the tooltip. */
    why?: () => string | null;
}

/**
 * The tools, in one fixed order, in every mode.
 *
 * Grouped: camera, then visibility, then history. Nothing here depends on which mode you
 * are in — that is the entry requirement.
 */
const RAIL_TOOLS: RailTool[] = [
    {id: "fit", icon: "expand", label: "Fit all", shortcut: "Shift+A", run: fitAll},
    {id: "focus", icon: "mode-inspect", label: "Focus selection", shortcut: "Shift+F", run: focusSelection},
    {id: "divider-1", icon: "expand", label: "", divider: true},
    {id: "hide", icon: "view-off", label: "Hide selection", shortcut: "Shift+H", run: hideSelection},
    {id: "unhide", icon: "view", label: "Unhide all", shortcut: "Shift+U", run: unhideAll},
    {id: "section", icon: "section-plane", label: "Section planes", run: openSectionPlanes},
    {id: "measure", icon: "measure", label: "Measure", pending: true},
    {id: "divider-2", icon: "expand", label: "", divider: true},
    // Universal, not modelling-specific. Greyed with a reason when there is no document
    // with a history — never hidden.
    {id: "undo", icon: "undo", label: "Undo", shortcut: "Ctrl+Z", run: undo, why: builderOpen},
    {id: "redo", icon: "redo", label: "Redo", shortcut: "Shift+Z", run: redo, why: builderOpen},
];

/** Undo/redo currently only have a history to act on inside the procedural builder. */
function builderOpen(): string | null {
    return useCellBuilderStore.getState().active !== null ? null : "Nothing to undo here yet";
}

function openSectionPlanes(): void {
    useSceneInfoStore.getState().setMode("section");
    const {mode} = useModeStore.getState();
    useLayoutStore.getState().openPanel(mode, "scene", "right");
}

export default function ToolRail() {
    // Subscribes to the builder so undo/redo re-evaluate their greyed state when a
    // procedural model opens or closes. Without this the rail would be correct only
    // until the next unrelated re-render.
    useCellBuilderStore((st) => st.active);

    return (
        <nav
            aria-label="Tools"
            style={{gridArea: "rail", zIndex: Z.dock}}
            className="flex flex-col items-center gap-1 shrink-0 w-11 py-1.5 bg-surface-0 border-r border-edge overflow-y-auto scrollbar"
        >
            {RAIL_TOOLS.map((t) => (t.divider ? <Divider key={t.id} /> : <RailButton key={t.id} tool={t} />))}
        </nav>
    );
}

function RailButton({tool}: {tool: RailTool}) {
    // Three ways a tool can be unusable, and each says something different in the
    // tooltip. "Greyed with no explanation" is the thing that makes people give up on a
    // control rather than look for the state it needs.
    const notWired = tool.pending || !tool.run;
    const reason = notWired ? "not wired up yet" : (tool.why?.() ?? null);
    const disabled = reason != null;
    return (
        <IconButton
            size="md"
            disabled={disabled}
            onClick={tool.run}
            tooltip={
                disabled
                    ? `${tool.label} — ${reason}`
                    : `${tool.label}${tool.shortcut ? ` (${tool.shortcut})` : ""}`
            }
            icon={<Icon name={tool.icon} />}
            className={cn(disabled && "opacity-35")}
        />
    );
}

function Divider() {
    return <span aria-hidden="true" className="shrink-0 w-6 h-px my-0.5 bg-edge" />;
}
