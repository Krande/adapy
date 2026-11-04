// Layout as data: presets, zones and the per-user document that records where
// panels are. TYPES ONLY in phase 0 -- nothing reads or writes a layout yet, and
// the classic UI renders exactly as before. They are defined now so that the
// panel contract (`./panel`) and the presets that consume it are designed
// together, and so the shapes are fixed before anything is persisted.
//
// The model, briefly:
//
//   * A PRESET is a whole arrangement of zones around the viewport. Core ships
//     `classic` (today's UI) and, later, `docked`; a UI shell may contribute
//     more. Which preset is active is a user choice -- a "mode" -- and is
//     persisted with the rest of the user's layout.
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

/** The built-in presets. Shell presets use their own ids. */
export const CLASSIC_PRESET_ID: PresetId = "classic";
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
  /** Free floating windows (attribute search, node editor). */
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
  floats: Record<PanelId, FloatState>;
  minimised: PanelId[];
  /** Panels already offered once, so a panel registered after the document was saved is adopted once. */
  known: PanelId[];
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
  /** The selected preset -- the user's "mode". Absent: the deployment / build default. */
  activePreset?: PresetId;
  /**
   * Show undock / re-dock affordances in the classic preset. Off by default,
   * which keeps the classic UI exactly as it is.
   */
  customisablePanels?: boolean;
  /** Documents by `layoutDocKey`. */
  docs: Record<string, LayoutDoc>;
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
