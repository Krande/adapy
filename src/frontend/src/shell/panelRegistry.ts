import React from "react";
import type {IconName} from "@/components/icons";
import type {DockId} from "./regions";
import type {ModeId} from "./modeStore";
import {runtime} from "@/runtime/config";
// Straight from the registry module, NOT the @/plugins barrel: the barrel re-exports the
// slot components, which reach stores, which reach the model worker — and any test that
// imports this file then dies on ?worker&inline. Sixth occurrence of that trap.
import {hasSimulationContributors} from "@/plugins/registry";
import {useMeStore} from "@/state/meStore";

// The panel catalogue.
//
// Every one of the ~25 independent visibility booleans scattered across as many stores
// becomes one entry here. That is the point: the old toolbar was ten identical icon
// buttons each wired to a different store field, with no list of what existed. This IS
// the list — the mode rail, the command palette and the shortcuts reference are all
// generated from it, so they cannot disagree with each other or go stale.
//
// src/__tests__/shell/panelRegistry.test.ts pins the ids against the feature inventory:
// a panel that goes missing during the rewrite fails CI rather than quietly vanishing.

export interface PanelDef {
    id: PanelId;
    /** Tab label. Short — these sit in a scrolling strip. */
    title: string;
    icon: IconName;
    /** Modes offering this panel in their rail. "all" for the mode-independent ones. */
    modes: readonly ModeId[] | "all";
    defaultDock: DockId;
    /** Open by default in the modes that offer it. Most panels are not. */
    defaultOpen?: boolean;
    /** May be pinned to survive mode switches. */
    pinnable?: boolean;
    /**
     * Runtime gate — e.g. Storage only exists in REST mode. Evaluated on render, not at
     * module load, because runtime.* reads window globals injected by /config.js.
     */
    available?: () => boolean;
    /** Keyboard shortcut, in the shortcuts.ts vocabulary. Feeds tooltip + palette. */
    shortcut?: string;
    /** One line for the command palette and the rail tooltip. */
    hint?: string;
    /**
     * Props are `any` deliberately. A panel is mounted by the shell with NO props — the
     * contract is that panels read their own stores — but the concrete components vary
     * (some declare optional props, e.g. SimulationControls), and narrowing this to
     * `never` makes every registration a variance error for no safety gain.
     */
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    component: React.LazyExoticComponent<React.ComponentType<any>>;
}

export const PANEL_IDS = [
    "outliner",
    "properties",
    "scene",
    "simulation",
    "component-build",
    "fea-table",
    "cellbuilder",
    "builder-components",
    "node-editor",
    "convert",
    "admin",
] as const;

export type PanelId = (typeof PANEL_IDS)[number];

// Lazy so a mode that never opens a panel never pays for it, and so the chunk-split
// hosted build can keep Storage out of the desktop bundle's critical path.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const lazy = (f: () => Promise<{default: React.ComponentType<any>}>) =>
    React.lazy(f) as PanelDef["component"];

export const PANELS: Record<PanelId, PanelDef> = {
    outliner: {
        id: "outliner",
        title: "Outliner",
        icon: "tree",
        // Mode-independent: you can always see what is in the model. Non-modality.
        modes: "all",
        defaultDock: "left",
        defaultOpen: true,
        pinnable: true,
        shortcut: "Shift+T",
        hint: "Model hierarchy",
        component: lazy(() => import("@/components/tree_view/TreeViewComponent")),
    },
    properties: {
        id: "properties",
        title: "Properties",
        icon: "info",
        // Follows selection in every mode — the Maya Attribute Editor idea, and the
        // replacement for the N bespoke info boxes.
        modes: "all",
        defaultDock: "right",
        defaultOpen: true,
        pinnable: true,
        hint: "Selected object",
        component: lazy(() => import("@/components/properties/PropertiesPanel")),
    },
    scene: {
        id: "scene",
        title: "Scene",
        icon: "scene",
        // Every mode. The Scene panel holds the clip planes, the take-off and the mesh
        // tools — all of which describe the loaded geometry, which is present in every
        // mode including the Library. Excluding it there meant the rail's "Section
        // planes" button had nowhere to open.
        modes: "all",
        defaultDock: "right",
        pinnable: true,
        hint: "Loaded models, sections, quantities, mesh QA",
        component: lazy(() => import("@/components/info_box_scene/ScenePanel")),
    },
    // Plugin tabs only.
    //
    // Its built-in content — field, component, step, deform scale, colormap — is in the
    // Results toolbar's display popover now, and the transport went there earlier. What
    // is left is the host for plugin `fem-sidebar` panels, which have nowhere else to
    // go, so the panel stays registered and simply is not offered when no plugin
    // contributes one. Deleting it outright would have dropped plugin panels silently,
    // which is inventory row B11 all over again.
    simulation: {
        id: "simulation",
        title: "Simulation",
        icon: "play",
        modes: ["results"],
        defaultDock: "right",
        defaultOpen: false,
        available: hasSimulationContributors,
        hint: "Plugin-contributed result views",
        component: lazy(() => import("@/components/simulation/SimulationControls")),
    },
    // Inventory row B9. It was a top-toolbar toggle in the classic UI, its store flag
    // was never re-homed, and nothing rendered the component afterwards: the file, its
    // store, its build pipeline and its service all survived the rewrite, unreachable.
    // Exactly the silent loss the parity checklist exists to catch, and invisible from
    // the outside — nothing throws when a component is simply never rendered.
    "component-build": {
        id: "component-build",
        title: "Connections",
        icon: "component",
        modes: ["build"],
        defaultDock: "right",
        defaultOpen: false,
        available: () => runtime.isRestMode(),
        hint: "Build a connection from a registered spec",
        component: lazy(() => import("@/components/component_view/ComponentControls")),
    },
    "builder-components": {
        id: "builder-components",
        title: "Model",
        icon: "cellbuilder",
        modes: ["build"],
        // Left dock, beside the viewport — where a model tree belongs, and where the
        // Outliner puts the same idea for loaded geometry. It was a collapsed disclosure
        // inside the Builder panel, sharing a narrow right dock with the settings that
        // control how the model compiles: the document and the knobs about the document
        // are different kinds of thing and were competing for one column.
        defaultDock: "left",
        defaultOpen: true,
        hint: "Cells and equipment in the procedural model",
        component: lazy(() => import("@/components/viewer/cellbuilder/ComponentsPanel")),
    },
    cellbuilder: {
        id: "cellbuilder",
        title: "Builder",
        icon: "cellbuilder",
        modes: ["build"],
        defaultDock: "right",
        defaultOpen: true,
        hint: "Cells, equipment, systems and detailing",
        component: lazy(() => import("@/components/viewer/CellBuilderPanel")),
    },
    "node-editor": {
        id: "node-editor",
        title: "Procedures",
        icon: "graph",
        modes: ["build"],
        // Bottom rather than a side dock: a node graph is wide, and the classic UI's
        // 800x600 floating window was covering the very model the procedures act on.
        defaultDock: "bottom",
        hint: "Run procedures over file objects",
        component: lazy(() => import("@/components/node_editor/NodeEditorPanel")),
    },
    "fea-table": {
        id: "fea-table",
        title: "Data",
        icon: "fem-data",
        modes: ["results"],
        // THE BOTTOM DOCK, and this is the point of having one. The result table is
        // wide and short — dozens of columns, a handful of rows in view. Floated over
        // the model (where it lives today) it covers exactly the geometry you are
        // reading it against; across the bottom it costs height the 3D does not need.
        defaultDock: "bottom",
        hint: "Result values per node and element",
        component: lazy(() => import("@/components/simulation/SimulationDataInfoPanel")),
    },
    convert: {
        id: "convert",
        title: "Convert",
        icon: "convert",
        // Its own mode. As a Library panel it shared the dock with the file browser you
        // pick sources from, so choosing a file and choosing what to do with it competed
        // for one column — and converting is a different activity from browsing anyway:
        // you arrive with an intent, not to look around.
        modes: ["convert"],
        defaultDock: "right",
        defaultOpen: true,
        available: () => runtime.isRestMode(),
        hint: "Convert uploaded files to other formats",
        component: lazy(() => import("@/components/convert/ConvertPage")),
    },
    admin: {
        id: "admin",
        title: "Admin",
        icon: "settings",
        modes: "all",
        // Bottom dock: 14 tabs of tables want width, not a 360px column.
        defaultDock: "bottom",
        // Admin-only AND REST-only. AdminPanel gates internally too, but a panel that
        // renders "you are not an admin" is worse than one that is simply not offered.
        available: () => runtime.isRestMode() && useMeStore.getState().isAdmin,
        hint: "Audit runs, workers, projects, corpora and system settings",
        component: lazy(() => import("@/components/admin/AdminPanel")),
    },
};

export const ALL_PANELS: readonly PanelDef[] = PANEL_IDS.map((id) => PANELS[id]);

export const isPanelId = (v: unknown): v is PanelId => PANEL_IDS.includes(v as PanelId);

export const panelDef = (id: PanelId): PanelDef => PANELS[id];

/** Does this panel exist at all in the current runtime? */
export const panelAvailable = (def: PanelDef): boolean => def.available?.() ?? true;

/** Panels a mode offers, in registry order, filtered by runtime availability. */
export function panelsForMode(mode: ModeId): readonly PanelDef[] {
    return ALL_PANELS.filter(
        (p) => panelAvailable(p) && (p.modes === "all" || p.modes.includes(mode)),
    );
}

/**
 * Resolve a panel id that may have come from persisted layout state.
 *
 * Returns null for an id that no longer exists or is unavailable in this runtime, so a
 * stale `ada:layout:v2` blob (or a REST-only panel in a desktop build) degrades to an
 * empty dock rather than a crash.
 */
export function resolvePanel(id: string): PanelDef | null {
    if (!isPanelId(id)) return null;
    const def = PANELS[id];
    return panelAvailable(def) ? def : null;
}
