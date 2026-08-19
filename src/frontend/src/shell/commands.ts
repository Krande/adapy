import {MODES, useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {panelsForMode, PANELS, panelAvailable, type PanelId} from "./panelRegistry";
import {keysFor} from "./shortcuts";
import {fitAll, focusSelection, hideSelection, unhideAll} from "./inspectActions";
import {openFemConcepts, toggleDataTable, toggleLegend} from "./resultsActions";
import {compilePreview, redo, undo} from "./buildActions";
import {openConvert, openUpload, refreshFiles} from "./dataActions";
import type {IconName} from "@/components/icons";
import type {Command} from "./commandFilter";

// Everything the palette can run.
//
// GENERATED from the registries rather than hand-listed. That is the whole point: a
// hand-written command list is a fourth copy of facts that already exist in the panel
// registry, the mode list and the shortcut registry — and it would be the copy that goes
// stale, because nothing breaks when it does. Add a panel and it appears here; add a
// shortcut and its keys show up beside the command.

/** Actions, grouped by the mode whose rail offers them. Mirrors ToolRail's MODE_TOOLS. */
const ACTIONS: {id: string; title: string; icon: IconName; shortcut?: string; keywords?: string; run: () => void}[] = [
    {id: "fit-all", title: "Fit all to view", icon: "expand", shortcut: "fit-all", keywords: "zoom frame extents", run: fitAll},
    {id: "focus-selection", title: "Centre on selection", icon: "mode-inspect", shortcut: "focus-selection", keywords: "zoom to", run: focusSelection},
    {id: "hide-selection", title: "Hide selection", icon: "view-off", shortcut: "hide-selection", keywords: "isolate", run: hideSelection},
    {id: "unhide-all", title: "Unhide all", icon: "view", shortcut: "unhide-all", keywords: "show reveal", run: unhideAll},
    {id: "toggle-legend", title: "Toggle the colour legend", icon: "filter", keywords: "scale colours", run: toggleLegend},
    {id: "toggle-data-table", title: "Toggle the result data table", icon: "fem-data", keywords: "values nodes", run: toggleDataTable},
    {id: "fem-concepts", title: "Show FEM concepts", icon: "group", keywords: "masses boundary conditions loads", run: openFemConcepts},
    {id: "undo", title: "Undo", icon: "undo", shortcut: "builder-undo", run: undo},
    {id: "redo", title: "Redo", icon: "redo", shortcut: "builder-redo", run: redo},
    {id: "compile-preview", title: "Compile preview", icon: "reload", shortcut: "compile-preview", keywords: "build procedural", run: compilePreview},
    {id: "upload", title: "Upload files", icon: "upload", keywords: "import add", run: openUpload},
    {id: "convert", title: "Convert files", icon: "reload", keywords: "export format glb ifc step", run: openConvert},
    {id: "refresh-files", title: "Refresh the file list", icon: "reload", run: refreshFiles},
];

/**
 * Build the command list for the current state.
 *
 * Panel commands are scoped to the CURRENT mode — offering "open the Builder" while in
 * Results would either do nothing or silently switch mode, and silent mode switches are
 * exactly what the non-modality contract forbids. Mode commands are always available;
 * switching mode is the one thing the palette may do to your workspace.
 */
export function buildCommands(): Command[] {
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

    // Panels this mode offers.
    for (const p of panelsForMode(mode)) {
        commands.push({
            id: `panel:${p.id}`,
            title: `${isOpen(p.id) ? "Hide" : "Show"} ${p.title}`,
            context: "Panel",
            icon: p.icon,
            keys: p.shortcut,
            keywords: p.hint,
            run: () => layout.togglePanel(mode, p.id, p.defaultDock),
        });
    }

    // Actions. Filtered to those whose panel/context exists — an action that cannot do
    // anything is worse than a missing one, because the user tries it.
    for (const a of ACTIONS) {
        commands.push({
            id: `action:${a.id}`,
            title: a.title,
            context: "Action",
            icon: a.icon,
            keys: a.shortcut ? keysFor(a.shortcut) : undefined,
            keywords: a.keywords,
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

    return commands;
}

export {scoreCommand, filterCommands, type Command} from "./commandFilter";

export {PANELS};
