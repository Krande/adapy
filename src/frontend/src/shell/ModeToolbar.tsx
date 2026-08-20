import React from "react";
import {Icon, type IconName} from "@/components/icons";
import {IconButton, cn} from "@/components/ui";
import {useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {useSceneInfoStore, type SceneInfoMode} from "@/state/sceneInfoStore";
import {openFemConcepts, toggleDataTable, toggleLegend} from "./resultsActions";
import {compilePreview} from "./buildActions";
import {openConvert, openUpload, refreshFiles} from "./dataActions";
import {useCellBuilderStore, type GizmoMode} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {runtime} from "@/runtime/config";
import {gizmoReason} from "./gizmoRules";

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
    /** Renders a rule instead of a button — grouping stays in the data. */
    divider?: boolean;
    /** Sunken when true. For tools that set a persistent state rather than fire once. */
    pressed?: () => boolean;
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

/**
 * Open the Scene panel at one of its tabs.
 *
 * Several toolbar entries are "show me X", where X is already a Scene-panel tab. They
 * route here rather than duplicating the UI: one implementation, several doors. That is
 * the same rule the marking menu and the command palette follow.
 */
function openScene(tab: SceneInfoMode): () => void {
    return () => {
        useSceneInfoStore.getState().setMode(tab);
        const {mode} = useModeStore.getState();
        useLayoutStore.getState().openPanel(mode, "scene", "right");
    };
}

/** Gizmo toggles drive cellBuilderStore directly — the same state G/R/S set. */
function setGizmo(g: Exclude<GizmoMode, "none">) {
    return () => {
        const cb = useCellBuilderStore.getState();
        // Pressing the active one turns it off, which is what a toggle in a toolbar
        // means and what Escape already does from the keyboard.
        cb.setGizmoMode(cb.gizmoMode === g ? "none" : g);
    };
}
const gizmoIs = (g: GizmoMode) => () => useCellBuilderStore.getState().gizmoMode === g;

/** Wires the pure rule in gizmoRules.ts to live store state. */
function needsGizmo(gizmo: "translate" | "rotate" | "resize") {
    return (): string | null => {
        const cb = useCellBuilderStore.getState();
        return gizmoReason(gizmo, {
            modelOpen: cb.active !== null,
            selectionKind: cb.selection === null ? null : (cb.cells[cb.selection.cellId]?.kind ?? null),
        });
    };
}

const div = (id: string): ModeTool => ({id, icon: "expand", label: "", divider: true});

const MODE_TOOLS: Record<ModeId, ModeTool[]> = {
    // Library — moving data across the boundary.
    data: [
        {id: "upload", icon: "upload", label: "Upload files", why: needsRest, run: openUpload},
        {id: "convert", icon: "convert", label: "Convert", why: needsRest, run: openConvert},
        {id: "refresh", icon: "reload", label: "Refresh", why: needsRest, run: refreshFiles},
    ],

    // Build — the gizmos first, because they are the tools you switch between constantly
    // while modelling. They are toggles, not actions: each sets a persistent state, so
    // each shows sunken while it is the active one. Same state G/R/S set from the
    // keyboard, so the toolbar and the keys cannot disagree.
    //
    // Cell placement stays a viewport gesture (click a face, drag to extrude) driven by
    // CellBuilderController. A button here would imply a tool mode that does not exist.
    build: [
        {id: "move", icon: "move", label: "Move", shortcut: "G", pressed: gizmoIs("translate"), why: needsGizmo("translate"), run: setGizmo("translate")},
        {id: "rotate", icon: "rotate", label: "Rotate", shortcut: "R", pressed: gizmoIs("rotate"), why: needsGizmo("rotate"), run: setGizmo("rotate")},
        {id: "resize", icon: "scale", label: "Resize", shortcut: "S", pressed: gizmoIs("resize"), why: needsGizmo("resize"), run: setGizmo("resize")},
        div("d1"),
        {id: "compile", icon: "reload", label: "Compile preview", shortcut: "Shift+Enter", why: needsBuilder, run: compilePreview},
        // No "Groups" here. It pointed at the Scene panel's Tools tab while the groups
        // it meant live under Model — and groups describe the loaded model, so they are
        // universal. The rail's Scene button is the one door onto all of that.
    ],

    // Inspect — routes into the Scene panel's tabs.
    //
    // The mode owns no exclusive machinery (see the note on the mode itself), but that is
    // not the same as having nothing to offer: interrogating a model IS the Scene panel,
    // and it was previously reachable only by opening the panel and finding the right
    // tab. These are doors onto tabs that already exist, not new UI.
    // Empty, and honestly so — for the second time, and for the same reason.
    //
    // It briefly held three buttons, each opening a different Scene-panel tab. Those tabs
    // describe the loaded geometry, which exists in every mode, so they were universal
    // tools wearing a mode strip's clothes; they are now one Scene button in the rail.
    //
    // What is left is nothing, because Inspect adds nothing: it is the base state, and
    // what it offers is the ABSENCE of the other modes' apparatus. Padding the strip to
    // make the mode look busy would be the third time this file learned the same lesson.
    inspect: [],

    // Results — playback first, then the readouts.
    //
    // Play/pause is here as well as on the Simulation panel's transport, and that is a
    // considered exception to "one control per piece of state": the transport lives on a
    // panel you may have closed, and a result set you cannot start without reopening a
    // panel is the kind of thing people file as a bug. Both drive isPlaying; neither
    // holds its own copy.
    results: [
        {id: "play", icon: "play", label: "Play / pause deformation", pressed: () => useFeaAnimationStore.getState().isPlaying, why: needsFea, run: togglePlay},
        div("d1"),
        {id: "legend", icon: "filter", label: "Colour legend", why: needsFea, run: toggleLegend},
        {id: "table", icon: "fem-data", label: "Result data table", why: needsFea, run: toggleDataTable},
        {id: "fem", icon: "group", label: "FEM concepts (masses, BCs)", why: needsFea, run: openFemConcepts},
    ],
};

function togglePlay(): void {
    const fea = useFeaAnimationStore.getState();
    fea.setIsPlaying(!fea.isPlaying);
}

// Section planes are NOT here. They were, appended to every mode except the Library —
// and they are also in the left rail, so the same tool sat in two places at once.
//
// Clipping applies to any geometry in any mode, which is exactly what the rail is for.
// Appending it per-mode was the old dynamic-palette habit surviving the move to a stable
// rail: the strip is for what a mode ADDS, and a tool every mode adds is not a mode tool.
export function toolsForMode(mode: ModeId): ModeTool[] {
    return MODE_TOOLS[mode] ?? [];
}

export default function ModeToolbar() {
    const mode = useModeStore((s) => s.mode);
    // Subscribe to everything the tools read, so greyed state AND sunken state re-render
    // when the underlying state moves — including when it moves from the keyboard.
    useCellBuilderStore((s) => s.active);
    useCellBuilderStore((s) => s.gizmoMode);
    useCellBuilderStore((s) => s.selection);
    useFeaAnimationStore((s) => s.sessionActive);
    useFeaAnimationStore((s) => s.isPlaying);

    const tools = toolsForMode(mode);
    if (tools.length === 0) return null;

    return (
        <div
            role="toolbar"
            aria-label={`${mode} tools`}
            className="flex min-w-0 items-center gap-0.5 overflow-x-auto scrollbar"
        >
            {tools.map((t) => {
                if (t.divider) {
                    return <span key={t.id} aria-hidden="true" className="mx-1 h-5 w-px shrink-0 bg-edge" />;
                }
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
                        pressed={t.pressed?.() ?? undefined}
                        icon={<Icon name={t.icon} size="sm" />}
                        className={cn(disabled && "opacity-40")}
                    />
                );
            })}
        </div>
    );
}
