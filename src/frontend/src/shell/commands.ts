import {MODES, useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {panelsForMode, PANELS, panelAvailable, type PanelId} from "./panelRegistry";
import {keysFor} from "./shortcuts";
import {fitAll, focusSelection, hideSelection, unhideAll} from "./inspectActions";
import {openFemConcepts, toggleDataTable, toggleLegend} from "./resultsActions";
import {compilePreview, redo, undo} from "./buildActions";
import {openConvert, openUpload, refreshFiles} from "./dataActions";
import {
    newProceduralModel,
    portsOverlayOn,
    recentreModel,
    representationIs,
    setRepresentation,
    sideBySideOn,
    superimposeOn,
    togglePortsOverlay,
    toggleSideBySide,
    toggleSuperimpose,
} from "./buildActions";
import {copyNames, hasSelection, selectChild, selectNextSibling, selectParent, selectPrevSibling} from "./selectionActions";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {runtime} from "@/runtime/config";
import {showAbout, showShortcuts} from "./HelpDialogs";
import {openSettings} from "@/components/options/SettingsDialog";
import type {IconName} from "@/components/icons";
import type {Command} from "./commandFilter";

// Everything the palette can run.
//
// GENERATED from the registries rather than hand-listed. That is the whole point: a
// hand-written command list is a fourth copy of facts that already exist in the panel
// registry, the mode list and the shortcut registry — and it would be the copy that goes
// stale, because nothing breaks when it does. Add a panel and it appears here; add a
// shortcut and its keys show up beside the command.

// Why a command might be greyed. Evaluated fresh every time the list is built, so the
// menu reflects the state at the moment you opened it.
//
// The menu shows disabled commands rather than hiding them — that is the whole point of
// having a menu bar. A menu whose contents depend on mode is a menu you cannot learn,
// which is the complaint the per-mode tool rail earned.
const REASONS = {
    selection: () => (hasSelection() ? null : "Nothing is selected"),
    builder: () =>
        useCellBuilderStore.getState().active !== null ? null : "No procedural model is open",
    fea: () =>
        useFeaAnimationStore.getState().sessionActive ? null : "No result set is loaded",
    rest: () => (runtime.isRestMode() ? null : "Only available in the hosted viewer"),
};

/** Actions, grouped by the mode whose rail offers them. Mirrors ToolRail's MODE_TOOLS. */
const ACTIONS: {
    id: string;
    title: string;
    icon: IconName;
    shortcut?: string;
    keywords?: string;
    /** Returns null when runnable, else why it is greyed. */
    why?: () => string | null;
    /** True when the thing this toggles is currently on. */
    checked?: () => boolean;
    /** Title to use while `checked`. */
    checkedTitle?: string;
    run: () => void;
}[] = [
    {id: "fit-all", title: "Fit all to view", icon: "expand", shortcut: "fit-all", keywords: "zoom frame extents", run: fitAll},
    {id: "focus-selection", title: "Centre on selection", icon: "mode-inspect", shortcut: "focus-selection", keywords: "zoom to", why: REASONS.selection, run: focusSelection},
    {id: "hide-selection", title: "Hide selection", icon: "view-off", shortcut: "hide-selection", keywords: "isolate", why: REASONS.selection, run: hideSelection},
    {id: "unhide-all", title: "Show all", icon: "show-all", shortcut: "unhide-all", keywords: "show reveal", run: unhideAll},
    {id: "toggle-legend", title: "Toggle the colour legend", icon: "filter", keywords: "scale colours", why: REASONS.fea, run: toggleLegend},
    {id: "toggle-data-table", title: "Toggle the result data table", icon: "fem-data", keywords: "values nodes", why: REASONS.fea, run: toggleDataTable},
    {id: "fem-concepts", title: "Show FEM concepts", icon: "group", keywords: "masses boundary conditions loads", why: REASONS.fea, run: openFemConcepts},
    {id: "undo", title: "Undo", icon: "undo", shortcut: "builder-undo", why: REASONS.builder, run: undo},
    {id: "redo", title: "Redo", icon: "redo", shortcut: "builder-redo", why: REASONS.builder, run: redo},
    {id: "compile-preview", title: "Compile preview", icon: "reload", shortcut: "compile-preview", keywords: "build procedural", why: REASONS.builder, run: compilePreview},
    {id: "upload", title: "Upload files", icon: "upload", keywords: "import add", why: REASONS.rest, run: openUpload},
    {id: "convert", title: "Convert files", icon: "convert", keywords: "export format glb ifc step", why: REASONS.rest, run: openConvert},
    {id: "refresh-files", title: "Refresh the file list", icon: "reload", why: REASONS.rest, run: refreshFiles},

    // Builder view state, from the Builder panel's old "View" tab. A checkmark-style
    // title ("Showing X" / "Show X") rather than a separate pressed affordance, because a
    // menu item's own label is where a menu says what state something is in.
    {id: "rep-topology", title: "Representation: Topology", icon: "cellbuilder", keywords: "cells editable model view", why: REASONS.builder, run: setRepresentation("topology"), checked: representationIs("topology"), checkedTitle: "✓ Representation: Topology"},
    {id: "rep-simulation", title: "Representation: Simulation", icon: "mode-results", keywords: "compiled analysis plates beams view", why: REASONS.builder, run: setRepresentation("simulation"), checked: representationIs("simulation"), checkedTitle: "✓ Representation: Simulation"},
    {id: "rep-detail", title: "Representation: Detail", icon: "component", keywords: "high fidelity joints girder view", why: REASONS.builder, run: setRepresentation("detail"), checked: representationIs("detail"), checkedTitle: "✓ Representation: Detail"},
    {id: "superimpose", title: "Superimpose topology under result", icon: "scene", keywords: "overlay cells under compiled", why: REASONS.builder, run: toggleSuperimpose, checked: superimposeOn, checkedTitle: "✓ Superimpose topology under result"},
    {id: "side-by-side", title: "Side-by-side: result beside topology", icon: "dock-right", keywords: "compare offset", why: REASONS.builder, run: toggleSideBySide, checked: sideBySideOn, checkedTitle: "✓ Side-by-side: result beside topology"},
    {id: "ports-overlay", title: "Port overlay", icon: "system-catalog", keywords: "equipment inputs outputs arrows", why: REASONS.builder, run: togglePortsOverlay, checked: portsOverlayOn, checkedTitle: "✓ Port overlay"},
    {id: "recentre", title: "Recentre the model", icon: "expand", keywords: "placement centre skewed", why: REASONS.builder, run: recentreModel},

    {id: "new-procedural", title: "New procedural model…", icon: "plus", keywords: "create build cellbuilder", why: REASONS.rest, run: () => void newProceduralModel()},

    // Selection. These were keyboard-only until the menu bar needed them by name.
    {id: "copy-names", title: "Copy selected names", icon: "copy", shortcut: "copy-names", keywords: "clipboard", why: REASONS.selection, run: copyNames},
    {id: "select-parent", title: "Select parent level", icon: "chevron", shortcut: "tree-parent", keywords: "up tree", why: REASONS.selection, run: selectParent},
    {id: "select-child", title: "Select first child", icon: "chevron", shortcut: "tree-child", keywords: "down tree", why: REASONS.selection, run: selectChild},
    {id: "select-prev", title: "Select previous sibling", icon: "chevron", shortcut: "tree-prev", why: REASONS.selection, run: selectPrevSibling},
    {id: "select-next", title: "Select next sibling", icon: "chevron", shortcut: "tree-next", why: REASONS.selection, run: selectNextSibling},
];

/**
 * Build the command list for the current state.
 *
 * Panel commands are scoped to the CURRENT mode — offering "open the Builder" while in
 * Results would either do nothing or silently switch mode, and silent mode switches are
 * exactly what the non-modality contract forbids. Mode commands are always available;
 * switching mode is the one thing the palette may do to your workspace.
 */
export function buildCommands(scope: "palette" | "menu" = "palette"): Command[] {
    const {mode, setMode} = useModeStore.getState();
    const layout = useLayoutStore.getState();

    const isOpen = (id: PanelId) => {
        const l = layout.perMode[mode];
        if (!l) return false;
        return (
            Object.values(l.docks).some((d) => !d.collapsed && d.tabs.includes(id)) ||
            id in l.floats ||
            l.overlays[id] === true
        );
    };

    const commands: Command[] = [];

    // Modes.
    for (const m of MODES) {
        if (m.id === mode) continue;
        commands.push({
            id: `mode:${m.id}`,
            title: `Switch to ${m.label}`,
            context: "Mode",
            icon: m.icon,
            keywords: m.hint,
            run: () => setMode(m.id as ModeId),
        });
    }

    // Panels.
    //
    // The palette offers this mode's panels only: "open the Builder" from Results would
    // either do nothing or switch mode behind your back, and silent mode switches are
    // what the non-modality contract forbids.
    //
    // The menu offers ALL of them, because a menu bar that changes with mode is the
    // problem it exists to solve. A panel belonging to another mode carries that mode's
    // name and switches to it when chosen — which is a mode switch the user asked for by
    // name, not one that happened to them.
    const panels =
        scope === "menu"
            ? MODES.flatMap((m) =>
                  panelsForMode(m.id as ModeId).map((p) => ({panel: p, home: m})),
              )
            : panelsForMode(mode).map((p) => ({panel: p, home: undefined}));

    const seen = new Set<string>();
    for (const {panel: p, home} of panels) {
        // A panel offered by several modes (Properties, Preferences) is listed once.
        if (seen.has(p.id)) continue;
        seen.add(p.id);
        const elsewhere = home != null && home.id !== mode;
        commands.push({
            id: `panel:${p.id}`,
            title: `${!elsewhere && isOpen(p.id) ? "Hide" : "Show"} ${p.title}`,
            context: elsewhere ? home!.label : "Panel",
            icon: p.icon,
            keys: p.shortcut,
            keywords: p.hint,
            enabled: panelAvailable(p),
            disabledReason: panelAvailable(p) ? undefined : "Not available in this deployment",
            run: () => {
                if (elsewhere) useModeStore.getState().setMode(home!.id as ModeId);
                const target = elsewhere ? (home!.id as ModeId) : mode;
                useLayoutStore.getState().togglePanel(target, p.id, p.defaultDock);
            },
        });
    }

    // Actions. Filtered to those whose panel/context exists — an action that cannot do
    // anything is worse than a missing one, because the user tries it.
    for (const a of ACTIONS) {
        const reason = a.why?.() ?? null;
        commands.push({
            id: `action:${a.id}`,
            // A menu item shows its state in its own text, so a toggle reads "Hide X"
            // once X is showing. The checked helper is per-action; most have none.
            title: a.checked?.() ? a.checkedTitle ?? a.title : a.title,
            context: "Action",
            icon: a.icon,
            keys: a.shortcut ? keysFor(a.shortcut) : undefined,
            keywords: a.keywords,
            enabled: reason == null,
            disabledReason: reason ?? undefined,
            run: a.run,
        });
    }

    // Layout housekeeping — reachable nowhere else, which is precisely the sort of thing
    // a palette is for.
    commands.push({
        id: "layout:reset-mode",
        title: `Reset the ${mode} layout`,
        context: "Layout",
        icon: "settings",
        keywords: "default arrangement docks",
        run: () => layout.resetMode(mode),
    });
    commands.push({
        id: "layout:reset-all",
        title: "Reset every mode's layout",
        context: "Layout",
        icon: "settings",
        keywords: "default arrangement docks",
        run: () => layout.resetAll(),
    });

    // Settings. A dialog, not a panel — so it is named for what it IS rather than
    // generated as "Show preferences", which is what the panel command produced.
    commands.push({
        id: "app:settings",
        title: "Settings…",
        context: "Application",
        icon: "settings",
        keys: keysFor("toggle-options"),
        keywords: "preferences options theme performance appearance",
        run: openSettings,
    });

    // Help. Present in both scopes: the palette is where people look for "shortcuts"
    // by name, the menu is where people look when they do not know the name.
    commands.push({
        id: "help:shortcuts",
        title: "Keyboard shortcuts",
        context: "Help",
        icon: "document",
        keywords: "keys bindings hotkeys",
        run: showShortcuts,
    });
    commands.push({
        id: "help:about",
        title: "About adapy",
        context: "Help",
        icon: "info",
        keywords: "version build commit",
        run: showAbout,
    });

    return commands;
}

export {scoreCommand, filterCommands, type Command} from "./commandFilter";

export {PANELS};
