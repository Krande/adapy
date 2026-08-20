import React from "react";
import {Icon, type IconName} from "@/components/icons";
import {IconButton, cn} from "@/components/ui";
import {useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {openFemConcepts, toggleDataTable, toggleLegend} from "./resultsActions";
import {compilePreview} from "./buildActions";
import {openConvert, openUpload, refreshFiles} from "./dataActions";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {runtime} from "@/runtime/config";

// The mode's own tools, as a horizontal strip directly under the mode switcher.
//
// These used to be in the left rail, which meant the rail's contents changed when you
// changed mode — so nothing had a fixed address and the rail could not be learned. Here
// the changing contents are directly beneath the control that changes them, which is
// what makes them legible: the strip is visibly part of the mode, not part of the app
// chrome. Cinema 4D puts the mode selector on top and its palette on the left; the
// palette works there because its modes share most of their tools. Ours do not.
//
// The rail keeps what is true in every mode — camera, visibility, undo/redo.

interface ModeTool {
    id: string;
    icon: IconName;
    label: string;
    shortcut?: string;
    /** Not wired yet: shown disabled with an honest tooltip rather than hidden. */
    pending?: boolean;
    /** Returns null when usable, else why it is greyed. */
    why?: () => string | null;
    run?: () => void;
}

const needsFea = () =>
    useFeaAnimationStore.getState().sessionActive ? null : "No result set is loaded";
const needsBuilder = () =>
    useCellBuilderStore.getState().active !== null ? null : "No procedural model is open";
const needsRest = () => (runtime.isRestMode() ? null : "Only available in the hosted viewer");

/** Reveal the section-plane UI where it already lives: the Scene panel's Clip tab. */
function openSectionPlanes(): void {
    useSceneInfoStore.getState().setMode("section");
    const {mode} = useModeStore.getState();
    useLayoutStore.getState().openPanel(mode, "scene", "right");
}

const MODE_TOOLS: Record<ModeId, ModeTool[]> = {
    // Files: everything here is about moving data across the boundary.
    data: [
        {id: "upload", icon: "upload", label: "Upload files", why: needsRest, run: openUpload},
        {id: "convert", icon: "reload", label: "Convert", why: needsRest, run: openConvert},
        {id: "refresh", icon: "reload", label: "Refresh file list", why: needsRest, run: refreshFiles},
    ],
    // Inspect owns no tool the other modes lack — see the note on the mode itself. Rather
    // than pad the strip to make the mode look busy, it stays empty and the strip
    // collapses. An empty toolbar is the honest rendering of "nothing extra here", which
    // is what this mode is FOR.
    inspect: [],
    build: [
        {id: "compile", icon: "reload", label: "Compile preview", shortcut: "Shift+Enter", why: needsBuilder, run: compilePreview},
        // Cell placement is a viewport gesture (click a face, drag to extrude) driven by
        // CellBuilderController, not a button — a toolbar button here would imply a tool
        // mode that does not exist.
    ],
    results: [
        {id: "legend", icon: "filter", label: "Colour legend", why: needsFea, run: toggleLegend},
        {id: "table", icon: "fem-data", label: "Result data table", why: needsFea, run: toggleDataTable},
        {id: "fem", icon: "group", label: "FEM concepts (masses, BCs)", why: needsFea, run: openFemConcepts},
        // Playback lives on the Simulation panel's transport, beside the step and mode
        // sliders it belongs with. A duplicate play button here would be a second control
        // for one piece of state.
    ],
};

/** Section planes are offered wherever the Scene panel is — Inspect, Build and Results. */
const SECTION_TOOL: ModeTool = {
    id: "section",
    icon: "section-plane",
    label: "Section planes",
    run: openSectionPlanes,
};

export function toolsForMode(mode: ModeId): ModeTool[] {
    const base = MODE_TOOLS[mode] ?? [];
    return mode === "data" ? base : [...base, SECTION_TOOL];
}

export default function ModeToolbar() {
    const mode = useModeStore((s) => s.mode);
    // Re-evaluate greyed state when the documents these tools act on come and go.
    useCellBuilderStore((s) => s.active);
    useFeaAnimationStore((s) => s.sessionActive);

    const tools = toolsForMode(mode);
    if (tools.length === 0) return null;

    return (
        <div
            role="toolbar"
            aria-label={`${mode} tools`}
            className="flex min-w-0 items-center gap-0.5 overflow-x-auto scrollbar"
        >
            {tools.map((t) => {
                const notWired = t.pending || !t.run;
                const reason = notWired ? "not wired up yet" : (t.why?.() ?? null);
                const disabled = reason != null;
                return (
                    <IconButton
                        key={t.id}
                        size="sm"
                        disabled={disabled}
                        onClick={t.run}
                        tooltip={
                            disabled
                                ? `${t.label} — ${reason}`
                                : `${t.label}${t.shortcut ? ` (${t.shortcut})` : ""}`
                        }
                        icon={<Icon name={t.icon} size="sm" />}
                        className={cn(disabled && "opacity-40")}
                    />
                );
            })}
        </div>
    );
}
