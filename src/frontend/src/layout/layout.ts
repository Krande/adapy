// Layout as data: presets, zones and the per-user document that records where
// panels are. Types only: the pure reducers over a document are `./reducers`,
// the store that holds the user's documents is `./layoutStore`, and the host
// that renders a preset is `./LayoutHost`. Defined beside the panel contract
// (`./panel`) so the two are designed together, and so the shapes are fixed
// before anything is persisted.
//
// The model, briefly:
//
//   * A PRESET is a whole arrangement of zones around the viewport. Core ships
//     `classic`, whose document also holds a split grid of panel groups around
//     the viewport (`./splitTree`); a UI shell may contribute more.
//   * A ZONE is a region of a preset that holds panels: a stack (the classic
//     menu column), a dock, the float layer, a bottom sheet, an overlay.
//   * A LAYOUT DOCUMENT is the user's arrangement within ONE preset (and
//     optionally one context of it): which panels are in which zone, sizes,
//     floating rects. Switching presets switches documents; it never rewrites
//     the other preset's document.
//   * `LayoutPrefs` bundles the active preset, the per-preset documents and the
//     user's layout settings into the one value a store persists. It is plain
//     JSON so the same value can live in localStorage or behind a per-user
//     preferences endpoint on the server (`LayoutPersistence`).
//
// Dependency-free: React appears `import type` only.

import type React from "react";
import type { PanelId, PresetId, RegisteredPanel, ZoneId } from "./panel";
import type { GridNode } from "./splitTree";
import type { RailPrefs } from "./toolRailModel";

/** The built-in preset. Shell presets use their own ids. */
export const CLASSIC_PRESET_ID: PresetId = "classic";
/** A placement key a plugin slot's `dock` is recorded under, for a shell that docks by it. Core has no such preset. */
export const DOCKED_PRESET_ID: PresetId = "docked";

/**
 * Zone ids of the classic preset, in the classic UI's terms. Panels in the core
 * catalogue name these in `placement.classic`.
 */
export const CLASSIC_ZONES = {
  /** The left tree track that pushes the menu column. */
  tree: "classic.tree",
  /** The top bar of toggles. Chrome, not a panel zone. */
  toolbar: "classic.toolbar",
  /** The column under the top bar where panels stack (a bottom sheet on phones). */
  menuColumn: "classic.menu-column",
  /** The single in-viewer window (admin / convert, one at a time). */
  window: "classic.window",
  /** Free floating windows (attribute search). */
  float: "classic.float",
  /** HUDs anchored to the canvas. */
  overlay: "classic.overlay",
} as const;

export type ZoneKind =
  /** Panels stacked in one column, each its own card (the classic menu column). */
  | "stack"
  /** A grid track at an edge; tabs or stacked; resizable; pushes the viewport. */
  | "dock"
  /** Free windows. */
  | "float"
  /** A phone bottom sheet; one active panel. */
  | "sheet"
  /** HUDs anchored to the canvas. */
  | "overlay";

/** A zone as a preset defines it. Static. */
export interface ZoneDef {
  id: ZoneId;
  kind: ZoneKind;
  label: string;
  /** For docks. */
  edge?: "left" | "right" | "bottom";
  /** Size limits along the zone's axis, in px. */
  limits?: { min: number; max: number; default: number };
  arrangement?: "tabs" | "stack";
}

/** One zone's persisted state. */
export interface ZoneState {
  panels: PanelId[];
  active: PanelId | null;
  size?: number;
  collapsed?: boolean;
}

/** A floating panel's persisted rect and stacking. */
export interface FloatState {
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  minimised?: boolean;
}

/** The user's arrangement within one preset (and context). Persisted, versioned. */
export interface LayoutDoc {
  v: number;
  preset: PresetId;
  /** A context within the preset (a shell's sub-mode); `"default"` when there is none. */
  context?: string;
  zones: Record<ZoneId, ZoneState>;
  /** The split grid around the viewport (`./splitTree`). Absent: the viewport alone. */
  grid?: GridNode;
  floats: Record<PanelId, FloatState>;
  minimised: PanelId[];
  /** Panels already offered once, so a panel registered after the document was saved is adopted once. */
  known: PanelId[];
  /** The composed shell's `layout.version` the document was made under. */
  shellVersion?: number;
}

/** A whole arrangement of zones around the viewport. */
export interface LayoutPreset {
  id: PresetId;
  label: string;
  /** One line for a preset switcher. */
  description?: string;
  /** `"core"` or the contributing plugin's id. */
  owner?: string;
  zones: ZoneDef[];
  /** The document a user starts from. */
  defaults(ctx: { context: string; panels: RegisteredPanel[] }): LayoutDoc;
  /**
   * The preset's root renderer, which arranges its zones around the viewport.
   * Lazy, React.lazy-compatible. The viewport is rendered exactly once and never
   * inside a movable container: the WebGL canvas cannot be re-parented.
   */
  frame?: () => Promise<{ default: React.ComponentType<{ children?: React.ReactNode }> }>;
  /**
   * Whether `PanelConstraints.exclusiveGroup` holds under the user's prefs:
   * revealing a panel closes the rest of its group. Omitted: always.
   */
  exclusiveGroups?: (prefs: LayoutPrefs) => boolean;
}

/** The key of a document in `LayoutPrefs.docs`: `<preset>` or `<preset>:<context>`. */
export function layoutDocKey(preset: PresetId, context?: string): string {
  return context && context !== "default" ? `${preset}:${context}` : preset;
}

/**
 * Everything layout-related a user has chosen, as one JSON value: the unit a
 * persistence backend stores. Per user on the server in hosted mode; per browser
 * where there is no server account (desktop, embed).
 */
export interface LayoutPrefs {
  v: number;
  /** A preset a user once selected. Kept for older saved prefs; core renders the classic preset whatever it says. */
  activePreset?: PresetId;
  /**
   * Before 1.14: the one "Customisable panels" setting for every UI. Read as core's
   * own UI's (`customisablePanelsByShell.core`) and moved there on load.
   */
  customisablePanels?: boolean;
  /**
   * Undock / re-dock affordances, by UI shell id: what the user chose in each UI
   * whose shell lets them choose (core's own; a composed shell requires them).
   * Absent for a shell: on (`customisablePanelsOn`).
   */
  customisablePanelsByShell?: Record<string, boolean>;
  /**
   * The user's keyboard shortcuts that differ from the defaults: shortcut id -> chord, or null for "no key".
   * Not per shell: a person's keys follow them across UIs. An id nothing registers is ignored (`./shortcuts`).
   */
  keyOverrides?: Record<string, string | null>;
  /** UI freeze (lock the arrangement) on or off, by UI shell id, where the user chose (`./uiFreeze`). Absent: the shell's default. */
  uiFreezeByShell?: Record<string, boolean>;
  /** Documents by `layoutDocKey`. */
  docs: Record<string, LayoutDoc>;
  /** A composed shell's pinned panels, by shell id: they stay open and placed across its modes. */
  pinned?: Record<string, PanelId[]>;
  /** A composed shell's named workspaces, by shell id then name: its documents (by `layoutDocKey`) as saved. */
  workspaces?: Record<string, Record<string, Record<string, LayoutDoc>>>;
  /** A composed shell's tool rail as the user arranged it, by shell id (`./toolRailModel`). Absent for a shell: the shell's own. */
  rail?: Record<string, RailPrefs>;
  /** The marking menu on or off, by UI shell id, where the user chose (`./markingMenuModel`). Absent: the shell's default. */
  markingMenuByShell?: Record<string, boolean>;
}

/**
 * The user's "Customisable panels" setting in UI shell `shell` (core's own UI by
 * default): on unless they turned it off there. Whether a shell lets them choose
 * at all is `customisablePanels()` (`./customisable`), which every reader goes through.
 */
export function customisablePanelsOn(
  prefs: Pick<LayoutPrefs, "customisablePanels" | "customisablePanelsByShell">,
  shell: string = "core",
): boolean {
  const own = prefs.customisablePanelsByShell?.[shell];
  if (typeof own === "boolean") return own;
  return shell === "core" ? prefs.customisablePanels !== false : true;
}

/**
 * Where `LayoutPrefs` live. Asynchronous so a server-backed implementation (a
 * per-user preferences endpoint) fits as well as localStorage; `load` resolves
 * `null` when nothing was saved yet.
 */
export interface LayoutPersistence {
  load(): Promise<LayoutPrefs | null>;
  save(prefs: LayoutPrefs): Promise<void>;
}
