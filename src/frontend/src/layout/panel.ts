// The panel contract: what a panel IS, independent of where a layout puts it.
//
// WHY THIS EXISTS
// ---------------
// Every panel of the classic UI couples four things in one component: its
// content, its chrome (frame, header, close button), where it sits (the menu
// column, a bottom sheet, a floating window) and whether it is visible (a flag
// in a domain store, read inside `Menu.tsx`). Nothing listed the panels, so any
// other host -- a UI shell, a command palette, a shortcuts dialog -- had to
// rediscover them, and lost some on the way.
//
// A `PanelSpec` names those four things separately. Phase 0 of the panel system
// only CATALOGUES the panels that exist (`./corePanels`) and lets callers open
// and close them by id (`./reveal`); nothing renders through this contract yet,
// so the classic UI is unchanged. The fields that a layout host will read later
// (placement, capabilities, constraints, `frame`) are here so that the core
// catalogue, plugins and UI shells describe their panels ONCE, in the shape the
// host will consume.
//
// Like `@/plugins/registry`, this module is dependency-free: React appears
// `import type` only, so it is erased at transpile and `node --test` can import
// it.

import type React from "react";

/** Globally unique panel id: `core:<name>` for core, `<pluginId>:<local>` for a plugin. */
export type PanelId = string;

/** A zone of a layout preset, e.g. `classic.menu-column`, `left`, `right`, `bottom`. */
export type ZoneId = string;

/** A layout preset id: `classic`, `docked`, or one a UI shell contributes. */
export type PresetId = string;

/** Who registered a panel: `"core"`, or the owning plugin's id. */
export type PanelOwnerId = string;

/**
 * What a layout may do with a panel. Every field defaults as documented; a spec
 * states only the exceptions. In the classic preset these are inert unless the
 * user turns on customisable panels (see `LayoutPrefs.customisablePanels`).
 */
export interface PanelCapabilities {
  /** The user may close it. Default true. */
  closable: boolean;
  /** It may be undocked to a floating window. Default true. */
  floatable: boolean;
  /** It may be placed in a dock zone. Default true. */
  dockable: boolean;
  /** It may open in a separate browser window. Default false; never for a panel that requires the canvas. */
  popout: boolean;
  /** Its host may resize it. Default true. */
  resizable: boolean;
  /** Stay mounted while hidden (expensive bodies: the node editor, a data table). Default false. */
  keepAlive: boolean;
  /** It may share a zone by vertical stacking (false for trees and tables). Default true. */
  stackable: boolean;
}

export const DEFAULT_PANEL_CAPABILITIES: Readonly<PanelCapabilities> = Object.freeze({
  closable: true,
  floatable: true,
  dockable: true,
  popout: false,
  resizable: true,
  keepAlive: false,
  stackable: true,
});

/** Where a panel can live at all. */
export interface PanelConstraints {
  /** Needs the 3D scene; excluded from canvas-less profiles (`/convert`, `/admin`, follower windows). */
  requiresCanvas?: boolean;
  /** Hosted (REST) mode only. */
  requiresRest?: boolean;
  /** Needs an `AdaViewerProvider` above it. */
  requiresViewerProvider?: boolean;
  minSize?: { w?: number; h?: number };
  maxSize?: { w?: number; h?: number };
  /** At most one panel of a group is open at a time (the in-viewer admin / convert window). */
  exclusiveGroup?: string;
}

/**
 * Where a panel goes in one layout preset.
 *
 * `parent` + `as` describe a panel that is hosted INSIDE another panel rather than
 * in a zone of its own: a plugin's Simulation tab, an admin tab. The classic UI
 * mounts those through the parent's own tab strip, and a layout host will too.
 */
export interface PanelPlacement {
  /** The preferred zone in this preset. */
  zone: ZoneId;
  /** Order within the zone. Core uses gaps of 10 so plugins can slot between. */
  order?: number;
  /** Initial size along the zone's axis / both axes. */
  size?: { w?: number; h?: number };
  /** First-open rect when the panel is floated. */
  float?: { w: number; h: number };
  /** The panel that hosts this one, when it is a tab or section of another panel. */
  parent?: PanelId;
  /** How the parent hosts it. Only meaningful with `parent`. */
  as?: "tab" | "inline" | "section";
}

/**
 * Placement per preset. A preset looks up its own id first, then `default`.
 * Keyed by id rather than fixed fields so presets contributed by UI shells fit
 * beside core's `classic` and `docked` without a contract change.
 */
export type PanelPlacements = Partial<Record<PresetId, PanelPlacement>> & {
  default?: PanelPlacement;
};

/**
 * Two-way binding to wherever "is this panel open" lives today.
 *
 * While a binding is present, the bound flag is the source of truth: every
 * existing opener (a top-bar button, a keyboard shortcut, domain code) keeps
 * working, and a layout host only reads and writes through it.
 */
export interface PanelBinding {
  isOpen(): boolean;
  setOpen(open: boolean): void;
  /** Notifies when `isOpen()` may have changed. Returns the unsubscribe. */
  subscribe(cb: () => void): () => void;
}

/** What a host tells a panel body about where it is mounted. */
export interface PanelHostInfo {
  kind: "stack" | "dock" | "float" | "sheet" | "window" | "overlay";
  zone: ZoneId;
}

/** Props a host passes to a panel body. A legacy body may ignore them. */
export interface PanelProps {
  panelId: PanelId;
  host: PanelHostInfo;
  close(): void;
  /** True when this panel is the visible tab in its zone. */
  isActive: boolean;
}

/** One action in a panel frame's header (float, dock to..., minimise, close, pop out). */
export interface PanelAction {
  id: string;
  label: string;
  icon?: React.FC;
  run(): void;
  disabled?: boolean;
}

/**
 * Props of a panel FRAME: the chrome around a body (header, title, actions,
 * close). One frame component is meant to serve core panels and a UI shell's
 * replacement frame alike, which is why the props are defined here, beside the
 * panel contract, rather than by any one implementation.
 */
export interface PanelFrameProps {
  panelId: PanelId;
  title: string;
  icon?: React.FC;
  host: PanelHostInfo;
  actions: readonly PanelAction[];
  onClose?: () => void;
  /**
   * In a floating host: the class the window is dragged by. The frame puts it on
   * its title bar, so the window moves by its header and nowhere else.
   */
  dragHandleClassName?: string;
  children?: React.ReactNode;
}

/**
 * Cheap, synchronous facts a spec's `available` may read. Open-ended so a host
 * can add facts without a contract change; a spec must treat an absent fact as
 * "unknown" and stay available.
 */
export interface PanelEnv {
  /** Hosted (REST) mode, as opposed to the desktop/websocket viewer. */
  restMode?: boolean;
  /** A static embed with no websocket session. */
  websocketDeactivated?: boolean;
  /** The active layout preset. */
  preset?: PresetId;
  [fact: string]: unknown;
}

/** A panel body module, React.lazy-compatible. */
export type PanelBodyLoader = () => Promise<{ default: React.ComponentType<PanelProps> }>;

export interface PanelSpec {
  id: PanelId;
  title: string;
  icon?: React.FC;
  /** Tooltip / palette line. */
  hint?: string;
  /** In one shared key vocabulary, e.g. `"Shift+T"`. Descriptive in phase 0: the key is bound elsewhere. */
  shortcut?: string;
  /** The panel's content, lazily loaded. */
  body: PanelBodyLoader;
  /** The existing visibility flag. Omitted: the layout owns visibility (from phase 1). */
  binding?: PanelBinding;
  /**
   * Is the panel offered right now. Cheap, synchronous, no I/O; may read a store's
   * current state (the classic top bar hides a button the same way). Omitted: always.
   */
  available?: (env: PanelEnv) => boolean;
  capabilities?: Partial<PanelCapabilities>;
  constraints?: PanelConstraints;
  placement?: PanelPlacements;
  /**
   * The classic top bar shows a toggle for this panel, at this position. Ordered
   * like `placement.order` (gaps of 10). Lets a host generate the same set of
   * toggles the classic `Menu.tsx` hand-writes. `subOrder` breaks ties within one
   * `order` (plugin buttons share one slot and keep their own `order` here), then id.
   */
  toolbar?: { order: number; subOrder?: number };
  /** The shell modes that offer this panel; omitted means every mode. */
  modes?: readonly string[];
  /**
   * Who draws the chrome. `"own"`: the body still renders its legacy frame, so a
   * host mounts it bare (the pixel-identity lever for the classic UI). `"host"`:
   * the body is content only and the host wraps it in a panel frame. Default `"own"`
   * until a panel's content and frame are split.
   */
  frame?: "own" | "host";
}

/** A spec as the registry holds it. */
export interface RegisteredPanel extends PanelSpec {
  owner: PanelOwnerId;
  capabilities: PanelCapabilities;
  frame: "own" | "host";
}

/** A spec's placement for a preset: its own entry, else `default`. */
export function placementFor(
  panel: Pick<PanelSpec, "placement">,
  preset: PresetId,
): PanelPlacement | undefined {
  return panel.placement?.[preset] ?? panel.placement?.default;
}
