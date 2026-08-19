import React from "react";
import type {IconName} from "@/components/icons";
import type {DockId} from "./regions";
import type {ModeId} from "./modeStore";
import {runtime} from "@/runtime/config";

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
    "fea-table",
    "storage",
    "preferences",
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
        modes: ["inspect", "results", "build"],
        defaultDock: "right",
        pinnable: true,
        hint: "Loaded models, sections, quantities, mesh QA",
        component: lazy(() => import("@/components/info_box_scene/SceneInfoBox")),
    },
    simulation: {
        id: "simulation",
        title: "Simulation",
        icon: "play",
        modes: ["results"],
        defaultDock: "right",
        defaultOpen: true,
        hint: "Result fields, deformation and playback",
        component: lazy(() => import("@/components/simulation/SimulationControls")),
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
    storage: {
        id: "storage",
        title: "Storage",
        icon: "server",
        modes: ["data"],
        defaultDock: "left",
        defaultOpen: true,
        // REST-only: the desktop/WS build has no scopes, no blobs and no upload.
        available: () => runtime.isRestMode(),
        hint: "Browse, upload and convert files",
        component: lazy(() => import("@/components/storage/StorageBrowser")),
    },
    preferences: {
        id: "preferences",
        title: "Preferences",
        icon: "settings",
        modes: "all",
        // Floating rather than docked: it is a settings surface you open, adjust and
        // dismiss, not something you work alongside. Docking it would permanently
        // spend viewport width on it.
        defaultDock: "float",
        shortcut: "Shift+Q",
        hint: "Display, theme, performance and conversion options",
        component: lazy(() => import("@/components/OptionsComponent")),
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
