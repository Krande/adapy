import React from "react";
import {Icon, type IconName} from "@/components/icons";
import {IconButton, cn} from "@/components/ui";
import PositionedMenu, {type KebabMenuItem} from "@/components/common/PositionedMenu";
import {typePickerItems} from "@/utils/cellbuilder/ports";
import {useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {useSceneInfoStore, type SceneInfoMode} from "@/state/sceneInfoStore";
import {openFemConcepts, stopPlayback, toggleDataTable, toggleLegend, togglePlay} from "./resultsActions";
import {addLoftMember, addModeIs, armAddMode, compilePreview, newProceduralModel} from "./buildActions";
import {useCellBuilderStore, type GizmoMode} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useSectionStore} from "@/state/sectionStore";
import {
    addPlane,
    clearPlanes,
    flipActivePlane,
    gizmoShown,
    needsActivePlane,
    needsPlane,
    toggleGizmo,
    useSectionTools,
} from "./sectionTools";
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
    /**
     * Opens a menu instead of firing.
     *
     * Openings and equipment need a TYPE before placement means anything. The panel's
     * buttons always did this; the first version of these toolbar buttons only armed the
     * mode, which silently placed whatever type happened to be selected last — a toolbar
     * that looks equivalent to the control it replaced but quietly does less.
     */
    menu?: () => KebabMenuItem[];
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
    // Build — the gizmos first, because they are the tools you switch between constantly
    // while modelling. They are toggles, not actions: each sets a persistent state, so
    // each shows sunken while it is the active one. Same state G/R/S set from the
    // keyboard, so the toolbar and the keys cannot disagree.
    //
    // Cell placement stays a viewport gesture (click a face, drag to extrude) driven by
    // CellBuilderController. A button here would imply a tool mode that does not exist.
    build: [
        // Starting a model comes first, because until you have one nothing else here is
        // usable. It used to live only in the Library's "+" menu — so the one place you
        // would look while in Build mode had no way to begin.
        {id: "new-model", icon: "plus", label: "New procedural model…", why: needsRest, run: () => void newProceduralModel()},
        div("d0"),

        // Placement. These arm a mode rather than firing once: you click in the scene to
        // place. Shown pressed while armed, and pressing the armed one disarms it, which
        // is what Escape already does.
        {id: "add-cell", icon: "cellbuilder", label: "Add cell — then click in the scene", pressed: addModeIs("add-cell"), why: needsBuilder, run: armAddMode("add-cell")},
        {
            id: "add-opening",
            icon: "component",
            label: "Add opening — pick a type, then click a wall",
            pressed: addModeIs("add-opening"),
            why: needsBuilder,
            run: armAddMode("add-opening"),
            menu: () => {
                const s = useCellBuilderStore.getState();
                if (!s.openingTypes.length) {
                    return [{key: "none", label: "No opening types", disabled: true, onClick: () => {}}];
                }
                return typePickerItems(s.openingTypes).map((it) => ({
                    key: it.key,
                    label: it.label,
                    onClick: () => {
                        s.setSelectedOpeningType(it.slug);
                        s.setMode("add-opening");
                    },
                }));
            },
        },
        {
            id: "add-equipment",
            icon: "equipment-catalog",
            label: "Add equipment — pick a type, then click in the scene",
            pressed: addModeIs("add-equipment"),
            why: needsBuilder,
            run: armAddMode("add-equipment"),
            menu: () => {
                const s = useCellBuilderStore.getState();
                if (!s.equipmentTypes.length) {
                    return [{key: "none", label: "No equipment types", disabled: true, onClick: () => {}}];
                }
                return typePickerItems(s.equipmentTypes).map((it) => ({
                    key: it.key,
                    label: it.label,
                    onClick: () => {
                        s.setSelectedEquipmentType(it.slug);
                        s.setMode("add-equipment");
                    },
                }));
            },
        },
        // A loft member appears at the origin immediately — an action, not a mode, so no
        // pressed state.
        {id: "add-loft", icon: "procedural", label: "Add loft member (L)", why: needsBuilder, run: addLoftMember},
        div("d1"),

        {id: "move", icon: "move", label: "Move", shortcut: "G", pressed: gizmoIs("translate"), why: needsGizmo("translate"), run: setGizmo("translate")},
        {id: "rotate", icon: "rotate", label: "Rotate", shortcut: "R", pressed: gizmoIs("rotate"), why: needsGizmo("rotate"), run: setGizmo("rotate")},
        {id: "resize", icon: "scale", label: "Resize", shortcut: "S", pressed: gizmoIs("resize"), why: needsGizmo("resize"), run: setGizmo("resize")},
        div("d2"),
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
    // Convert's controls all live inside the panel: pick a source, pick targets, go.
    // A strip above it would either duplicate them or hold nothing.
    convert: [],

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
        // The transport. These were also in the Simulation panel's own button row —
        // the third duplicated control group found this way, after section planes and
        // groups. The panel keeps the things that pick a VALUE (field, step, colormap,
        // deform scale); the toolbar takes the things that DO something.
        {id: "play", icon: "play", label: "Play / pause deformation", pressed: () => useFeaAnimationStore.getState().isPlaying, why: needsFea, run: togglePlay},
        {id: "stop", icon: "stop", label: "Stop and reset deformation to zero", why: needsFea, run: stopPlayback},
        div("d1"),
        {id: "legend", icon: "filter", label: "Colour legend", why: needsFea, run: toggleLegend},
        {id: "table", icon: "fem-data", label: "Result data table", why: needsFea, run: toggleDataTable},
        {id: "fem", icon: "group", label: "FEM concepts (masses, BCs)", why: needsFea, run: openFemConcepts},
    ],
};

// Section planes are NOT here. They were, appended to every mode except the Library —
// and they are also in the left rail, so the same tool sat in two places at once.
//
// Clipping applies to any geometry in any mode, which is exactly what the rail is for.
// Appending it per-mode was the old dynamic-palette habit surviving the move to a stable
// rail: the strip is for what a mode ADDS, and a tool every mode adds is not a mode tool.
/**
 * Clip tools, shown to the RIGHT of the mode's own tools while armed.
 *
 * Appended rather than substituted: clipping is a second activity layered on top of the
 * mode you are in, so the mode's tools must stay put while you do it.
 */
const SECTION_TOOLS: ModeTool[] = [
    {id: "sec-x", icon: "section-x", label: "Clip on X (plane through the model centre)", run: addPlane("x")},
    {id: "sec-y", icon: "section-y", label: "Clip on Y", run: addPlane("y")},
    {id: "sec-z", icon: "section-z", label: "Clip on Z", run: addPlane("z")},
    {id: "sec-flip", icon: "flip", label: "Flip which side is cut away", why: needsActivePlane, run: flipActivePlane},
    {
        id: "sec-gizmo",
        icon: "move",
        label: "Drag handle on the active plane",
        pressed: gizmoShown,
        why: needsPlane,
        run: toggleGizmo,
    },
    {id: "sec-clear", icon: "close", label: "Remove all section planes", why: needsPlane, run: clearPlanes},
];

export function toolsForMode(mode: ModeId): ModeTool[] {
    return MODE_TOOLS[mode] ?? [];
}

/** The full strip: the mode's tools, then the clip tools when they are armed. */
export function stripFor(mode: ModeId, sectionShown: boolean): ModeTool[] {
    const base = toolsForMode(mode);
    if (!sectionShown) return base;
    // A divider only when there is something to divide from — Inspect's strip is empty,
    // and a rule against the left edge reads as a rendering fault.
    return base.length ? [...base, div("sec-div"), ...SECTION_TOOLS] : SECTION_TOOLS;
}

export default function ModeToolbar() {
    const mode = useModeStore((s) => s.mode);
    // Which tool's menu is open, and the buttons to anchor them to.
    const [openMenu, setOpenMenu] = React.useState<string | null>(null);
    const btnRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});
    // Subscribe to everything the tools read, so greyed state AND sunken state re-render
    // when the underlying state moves — including when it moves from the keyboard.
    useCellBuilderStore((s) => s.active);
    useCellBuilderStore((s) => s.gizmoMode);
    useCellBuilderStore((s) => s.mode);
    useCellBuilderStore((s) => s.selection);
    useFeaAnimationStore((s) => s.sessionActive);
    useFeaAnimationStore((s) => s.isPlaying);

    const sectionShown = useSectionTools((s) => s.shown);
    // Re-render when the planes change, so the clip tools' greyed and pressed states
    // follow the scene rather than the last unrelated render.
    useSectionStore((s) => s.planes.length);
    useSectionStore((s) => s.activeId);
    useSectionStore((s) => s.gizmoVisible);

    const tools = stripFor(mode, sectionShown);
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
                    <React.Fragment key={t.id}>
                    <IconButton
                        ref={(el) => {
                            btnRefs.current[t.id] = el;
                        }}
                        size="sm"
                        disabled={disabled}
                        onClick={() => {
                            // An armed placement mode toggles off on a second press — no
                            // point offering a type picker to cancel something.
                            if (t.menu && !t.pressed?.()) {
                                setOpenMenu((v) => (v === t.id ? null : t.id));
                                return;
                            }
                            t.run?.();
                        }}
                        tooltip={
                            disabled
                                ? `${t.label} — ${reason}`
                                : `${t.label}${t.shortcut ? ` (${t.shortcut})` : ""}`
                        }
                        pressed={t.pressed?.() ?? undefined}
                        icon={<Icon name={t.icon} size="sm" />}
                        className={cn(disabled && "opacity-40")}
                    />
                    {t.menu && openMenu === t.id && (
                        <PositionedMenu
                            anchor={{kind: "rect", getRect: () => btnRefs.current[t.id]?.getBoundingClientRect()}}
                            onClose={() => setOpenMenu(null)}
                            items={t.menu().map((it) => ({
                                ...it,
                                onClick: () => {
                                    it.onClick();
                                    setOpenMenu(null);
                                },
                            }))}
                        />
                    )}
                    </React.Fragment>
                );
            })}
        </div>
    );
}
