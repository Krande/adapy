import React from "react";
import {Icon, IconButton, cn, type IconName} from "@/components/ui";
import {useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {panelsForMode} from "./panelRegistry";
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
}

/** Always present, always in the same order, in every mode. */
const PINNED_TOOLS: RailTool[] = [
    {id: "select", icon: "mode-inspect", label: "Select", shortcut: "Q"},
    {id: "move", icon: "move", label: "Move", shortcut: "G", pending: true},
    {id: "rotate", icon: "rotate", label: "Rotate", shortcut: "R", pending: true},
    {id: "scale", icon: "scale", label: "Scale", shortcut: "S", pending: true},
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
        {id: "isolate", icon: "view", label: "Isolate selection", shortcut: "Shift+H", pending: true},
        {id: "unhide", icon: "view-off", label: "Unhide all", shortcut: "Shift+U", pending: true},
        {id: "section", icon: "section-plane", label: "Section plane", pending: true},
        {id: "measure", icon: "measure", label: "Measure", pending: true},
    ],
    results: [
        {id: "field", icon: "mode-results", label: "Result field", pending: true},
        {id: "play", icon: "play", label: "Play / pause", pending: true},
        {id: "legend", icon: "filter", label: "Colour legend", pending: true},
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
    return (
        <IconButton
            size="md"
            disabled={tool.pending}
            tooltip={
                tool.pending
                    ? `${tool.label} — not wired up yet`
                    : `${tool.label}${tool.shortcut ? ` (${tool.shortcut})` : ""}`
            }
            icon={<Icon name={tool.icon} />}
            className={cn(tool.pending && "opacity-35")}
        />
    );
}

function Divider() {
    return <span aria-hidden="true" className="shrink-0 w-6 h-px my-0.5 bg-edge" />;
}
