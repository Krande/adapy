import React from "react";
import {Icon, IconButton, cn, type IconName} from "@/components/ui";
import {useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {panelsForMode} from "./panelRegistry";
import {fitAll, focusSelection, hideSelection, unhideAll} from "./inspectActions";
import {openFemConcepts, toggleDataTable, toggleLegend} from "./resultsActions";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {Z} from "./zIndex";

// The dynamic tool palette — Cinema 4D R25's central idea, and the direct answer to
// "too much on screen at once".
//
// Two bands:
//   * PINNED tools, identical in every mode. C4D keeps select/move/rotate/scale fixed
//     for exactly this reason: the things you reach for constantly must not move when
//     you change discipline.
//   * MODE tools, which swap entirely. This is where the reduction happens — cellbuilder
//     tools simply do not exist while you are reading FEA results.
//
// Panel toggles live here too, so the rail doubles as the mode's panel switcher without
// a separate row of buttons.

interface RailTool {
    id: string;
    icon: IconName;
    label: string;
    shortcut?: string;
    /** Not yet wired — rendered disabled with an honest tooltip rather than hidden, so
     *  the shape of the mode is visible during the rebuild. */
    pending?: boolean;
    /** Delegates to an existing handler. Never reimplements behaviour. */
    run?: () => void;
}

/** Always present, always in the same order, in every mode. */
const PINNED_TOOLS: RailTool[] = [
    // Fit and focus are camera actions every persona reaches for constantly, which is
    // exactly what C4D pins: the things you use in every discipline must not move when
    // you change discipline.
    {id: "fit", icon: "expand", label: "Fit all", shortcut: "Shift+A", run: fitAll},
    {id: "focus", icon: "mode-inspect", label: "Focus selection", shortcut: "Shift+F", run: focusSelection},
    {id: "hide", icon: "view-off", label: "Hide selection", shortcut: "Shift+H", run: hideSelection},
    {id: "unhide", icon: "view", label: "Unhide all", shortcut: "Shift+U", run: unhideAll},
];

/**
 * Per-mode tools.
 *
 * Populated as each mode's milestone lands (M3 Inspect, M4 Results, M5 Build, M6 Data);
 * entries are marked pending until their handler exists. Showing a disabled control with
 * a truthful tooltip beats an empty rail that makes the mode look unfinished — and beats
 * a live control that does nothing.
 */
const MODE_TOOLS: Record<ModeId, RailTool[]> = {
    inspect: [
        // Opens the Scene panel on its Clip tab rather than duplicating the
        // section-plane UI — one implementation, reachable from the rail.
        {id: "section", icon: "section-plane", label: "Section planes", run: openSectionPlanes},
        {id: "measure", icon: "measure", label: "Measure", pending: true},
    ],
    results: [
        {id: "legend", icon: "filter", label: "Colour legend", run: toggleLegend},
        {id: "table", icon: "fem-data", label: "Result data table", run: toggleDataTable},
        {id: "fem", icon: "group", label: "FEM concepts (masses, BCs)", run: openFemConcepts},
        // Playback lives on the Simulation panel's transport, where the step and mode
        // sliders it belongs with already are. A duplicate play button in the rail would
        // be a second control for one piece of state — the thing this rebuild is
        // removing, not adding.
    ],
    build: [
        {id: "add-cell", icon: "plus", label: "Add cell", pending: true},
        {id: "undo", icon: "undo", label: "Undo", shortcut: "Ctrl+Z", pending: true},
        {id: "redo", icon: "redo", label: "Redo", shortcut: "Shift+Z", pending: true},
    ],
    data: [
        {id: "upload", icon: "upload", label: "Upload", pending: true},
        {id: "convert", icon: "reload", label: "Convert", pending: true},
        {id: "search", icon: "search", label: "Find file", pending: true},
    ],
};

/** Reveal the section-plane UI where it already lives: the Scene panel's Clip tab. */
function openSectionPlanes(): void {
    useSceneInfoStore.getState().setMode("section");
    const {mode} = useModeStore.getState();
    useLayoutStore.getState().openPanel(mode, "scene", "right");
}

export default function ToolRail() {
    const mode = useModeStore((s) => s.mode);
    const togglePanel = useLayoutStore((s) => s.togglePanel);
    const layout = useLayoutStore((s) => s.perMode[mode]);

    const panels = panelsForMode(mode);
    const tools = MODE_TOOLS[mode] ?? [];

    const isOpen = (id: string) =>
        Boolean(
            layout &&
                (Object.values(layout.docks).some((d) => d.tabs.includes(id as never)) ||
                    id in layout.floats ||
                    layout.overlays[id as never] === true),
        );

    return (
        <nav
            aria-label={`${mode} tools`}
            style={{gridArea: "rail", zIndex: Z.dock}}
            className="flex flex-col items-center gap-1 shrink-0 w-11 py-1.5 bg-surface-0 border-r border-edge overflow-y-auto scrollbar"
        >
            {PINNED_TOOLS.map((t) => (
                <RailButton key={t.id} tool={t} />
            ))}

            <Divider />

            {tools.map((t) => (
                <RailButton key={t.id} tool={t} />
            ))}

            {tools.length > 0 && <Divider />}

            {/* Panel toggles. Generated from the registry — a panel added there appears
                here with no edit to this file. */}
            {panels.map((p) => (
                <IconButton
                    key={p.id}
                    size="md"
                    tooltip={`${p.title}${p.shortcut ? ` (${p.shortcut})` : ""}${p.hint ? ` — ${p.hint}` : ""}`}
                    icon={<Icon name={p.icon} />}
                    pressed={isOpen(p.id)}
                    onClick={() => togglePanel(mode, p.id, p.defaultDock)}
                />
            ))}
        </nav>
    );
}

function RailButton({tool}: {tool: RailTool}) {
    const disabled = tool.pending || !tool.run;
    return (
        <IconButton
            size="md"
            disabled={disabled}
            onClick={tool.run}
            tooltip={
                disabled
                    ? `${tool.label} — not wired up yet`
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
