// The marking menu: a radial menu of commands opened at the pointer over the 3D
// view. The pure half -- the shell's configuration, the default items, the
// geometry (which sector a pointer is in) and the gesture that opens it -- so
// all of it is tested under plain `node --test`. The component and the DOM
// wiring are `./MarkingMenu`.
//
// Why radial: an item's DIRECTION is what a user learns. After a few uses they
// press, flick and release without reading the menu, which only works while an
// item keeps its place; so slots are given explicitly (`null` keeps one empty)
// and a command that cannot run is greyed IN PLACE, with its reason, not dropped.
//
// THE GESTURE (`MarkingGesture`). The right button already does two things over
// the view: a click opens the Scene menu for what is under the cursor, and a
// drag pans the camera. The marking menu takes the third thing a right button
// can do, a HOLD: press without moving for `holdMs` (300 ms) and the menu opens
// at the pointer; then
//   * flick (move past `deadZonePx` towards an item) and release: that item runs;
//   * release without moving: the menu stays open, to be clicked (hover
//     highlights, a click on an item or in its sector runs it) or worked from the
//     keyboard (arrows choose, Enter runs, Escape closes).
// A right press that moves more than `moveTolerancePx` before the hold is a pan,
// as before; a quick right click is the Scene menu, as before. On a platform
// that fires `contextmenu` on the PRESS (macOS, Linux), core holds that event
// back until it knows which of the three it is, and hands it on for a click.
// Shift / Ctrl / Alt + right press are left alone, and so is the procedural
// builder (it has right-click menus of its own) and touch input.
//
// From the keyboard, core's `core:marking-menu` shortcut (Shift+E) opens it at
// the pointer when that is over the view, else at the view's centre.
//
// Dependency-free (React appears as a type only).

import type React from "react";

import { CORE_COMMAND_IDS as C, cameraViewCommandId } from "./coreCommandIds";

/** At most this many items go round the wheel; the rest go in `more`. */
export const MARKING_MENU_MAX_SECTORS = 8;

/** Compass directions of an eight-item wheel, clockwise from north. */
export const MARKING_DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;
export type MarkingDirection = (typeof MARKING_DIRECTIONS)[number];

/** A slot that opens a wheel of its own (a second marking menu) where it is chosen. */
export interface MarkingSubmenu {
  label: string;
  icon?: React.FC;
  items: readonly MarkingMenuItemDef[];
}

/** One slot of the wheel: a command id, an empty slot (`null`, keeps the others in place) or a submenu. */
export type MarkingMenuItemDef = string | null | MarkingSubmenu;

/** What the items may depend on when the menu opens. */
export interface MarkingMenuContext {
  /** The active mode of the shell (`activeShellMode()`), null without modes. */
  mode: string | null;
  /** An FEA result is loaded. */
  feaResult: boolean;
  /** How many draw ranges are selected. */
  selectionCount: number;
}

/** How the menu opens. */
export interface MarkingMenuGesture {
  /** Hold the right button this long without moving to open the menu. Default 300 ms. */
  holdMs?: number;
  /** Movement during the hold that makes the press a camera drag instead. Default 5 px. */
  moveTolerancePx?: number;
  /** Travel from the centre before a direction counts. Default 24 px. */
  deadZonePx?: number;
}

/** A shell's marking menu (`UiShellChrome.markingMenu`). */
export interface MarkingMenuConfig {
  /**
   * The wheel, clockwise from north: up to eight slots (more are moved to `more`).
   * A function is asked each time the menu opens. Absent: `defaultMarkingMenuItems`.
   */
  items?: readonly MarkingMenuItemDef[] | ((ctx: MarkingMenuContext) => readonly MarkingMenuItemDef[]);
  /** A short list under the wheel, for what does not fit (command ids). Absent: `defaultMarkingMenuMore`. */
  more?: readonly string[] | ((ctx: MarkingMenuContext) => readonly string[]);
  gesture?: MarkingMenuGesture;
}

export const MARKING_HOLD_MS = 300;
export const MARKING_MOVE_TOLERANCE_PX = 5;
export const MARKING_DEAD_ZONE_PX = 24;
/** The wheel's radius: where the items' labels sit. */
export const MARKING_RADIUS_PX = 96;

export function markingGestureOptions(g: MarkingMenuGesture | undefined): Required<MarkingMenuGesture> {
  const pos = (v: number | undefined, d: number) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : d);
  return {
    holdMs: pos(g?.holdMs, MARKING_HOLD_MS),
    moveTolerancePx: pos(g?.moveTolerancePx, MARKING_MOVE_TOLERANCE_PX),
    deadZonePx: pos(g?.deadZonePx, MARKING_DEAD_ZONE_PX),
  };
}

/** The camera views as a submenu (the slot south of the default wheel). */
export const MARKING_VIEWS_SUBMENU: MarkingSubmenu = {
  label: "Views",
  // Top north, isometric north-east, right east, front south, left west, back north-west.
  items: [
    cameraViewCommandId("top"),
    cameraViewCommandId("isometric"),
    cameraViewCommandId("right"),
    null,
    cameraViewCommandId("front"),
    null,
    cameraViewCommandId("left"),
    cameraViewCommandId("back"),
  ],
};
// Every named view but Bottom (that one is in the palette and View -> Camera).

/**
 * Core's wheel. Framing up (north), hiding to the west with its opposite (show
 * all) north-west, selection east, the camera views south. Fixed in every
 * context: a command that cannot run is greyed where it is.
 */
export function defaultMarkingMenuItems(_ctx?: MarkingMenuContext): MarkingMenuItemDef[] {
  return [
    C.fitAll, // N
    C.fitSelection, // NE
    C.selectAll, // E
    C.clearSelection, // SE
    MARKING_VIEWS_SUBMENU, // S
    C.hideOthers, // SW
    C.hideSelected, // W
    C.showAll, // NW
  ];
}

/** Core's list under the wheel: the FEA isolation while a result is loaded, then history. */
export function defaultMarkingMenuMore(ctx?: MarkingMenuContext): string[] {
  const fea = ctx?.feaResult ? [C.feaIsolateSelectionSets, C.feaClearIsolation] : [];
  return [...fea, C.undo, C.redo];
}

/** The wheel and the list for `ctx`: at most eight slots, what is beyond them moved to the list. */
export function resolveMarkingMenu(
  config: MarkingMenuConfig | undefined,
  ctx: MarkingMenuContext,
): { items: MarkingMenuItemDef[]; more: string[] } {
  const ask = <T>(v: T | ((c: MarkingMenuContext) => T) | undefined, fallback: (c: MarkingMenuContext) => T): T => {
    try {
      return v === undefined ? fallback(ctx) : typeof v === "function" ? (v as (c: MarkingMenuContext) => T)(ctx) : v;
    } catch (err) {
      console.warn("[marking-menu] the shell's items threw; using core's", err);
      return fallback(ctx);
    }
  };
  const all = [...ask(config?.items, defaultMarkingMenuItems)];
  const more = [...ask(config?.more, defaultMarkingMenuMore)];
  const items = all.slice(0, MARKING_MENU_MAX_SECTORS);
  for (const extra of all.slice(MARKING_MENU_MAX_SECTORS)) if (typeof extra === "string") more.push(extra);
  return { items, more: [...new Set(more)] };
}

/**
 * Whether a shell has a marking menu, and whether it is on unless the user
 * says otherwise. Core's own UI (no spec) offers it, off by default: its right
 * button has meant "Scene menu or pan" for long, so a user opts in (Options ->
 * Theme). A composed shell offers it only when its `chrome.markingMenu` is
 * given (on by default then): a shell that does not name it may have right-
 * button gestures of its own. `false` takes it away.
 */
export function markingMenuAvailability(chrome: { markingMenu?: MarkingMenuConfig | false } | undefined, composed: boolean): {
  available: boolean;
  defaultOn: boolean;
  config?: MarkingMenuConfig;
} {
  const cfg = chrome?.markingMenu;
  if (cfg === false) return { available: false, defaultOn: false };
  if (!composed) return { available: true, defaultOn: false, config: cfg };
  return cfg ? { available: true, defaultOn: true, config: cfg } : { available: false, defaultOn: false };
}

// ---------------------------------------------------------------------------
// Geometry.
// ---------------------------------------------------------------------------

/**
 * The sector a pointer at (`dx`, `dy`) from the centre points into, of `count`
 * equal sectors clockwise from north (screen y grows downwards); null inside
 * the dead zone. Sector `i` is centred on `i * 360 / count` degrees.
 */
export function sectorAt(dx: number, dy: number, count: number = MARKING_MENU_MAX_SECTORS, deadZone: number = MARKING_DEAD_ZONE_PX): number | null {
  if (count <= 0 || !Number.isFinite(dx) || !Number.isFinite(dy)) return null;
  if (Math.hypot(dx, dy) < deadZone) return null;
  const deg = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
  const size = 360 / count;
  return Math.floor((deg + size / 2) / size) % count;
}

/** Where slot `i` of `count` sits, relative to the centre, at `radius`. */
export function sectorOffset(i: number, count: number, radius: number = MARKING_RADIUS_PX): { x: number; y: number } {
  const a = ((i * 360) / Math.max(1, count)) * (Math.PI / 180);
  const r = (v: number) => Math.round(v * 1000) / 1000 + 0; // + 0: no -0
  return { x: r(Math.sin(a) * radius), y: r(-Math.cos(a) * radius) };
}

/** The compass name of slot `i` of an eight-slot wheel (for labels and tests). */
export function directionOf(i: number, count: number = MARKING_MENU_MAX_SECTORS): MarkingDirection | null {
  if (count !== MARKING_MENU_MAX_SECTORS) return null;
  return MARKING_DIRECTIONS[((i % 8) + 8) % 8];
}

/** The next slot from `from` in `step` (+1 clockwise, -1 back) that holds something, over the wheel then the list. */
export function stepSelection(filled: readonly boolean[], from: number | null, step: 1 | -1): number | null {
  const n = filled.length;
  if (!n || !filled.some(Boolean)) return null;
  let i = from ?? (step > 0 ? -1 : n);
  for (let k = 0; k < n; k++) {
    i = (((i + step) % n) + n) % n;
    if (filled[i]) return i;
  }
  return null;
}

// ---------------------------------------------------------------------------
// The gesture.
// ---------------------------------------------------------------------------

export interface MarkingGestureOptions {
  holdMs: number;
  moveTolerancePx: number;
  /** Open the menu, held, at the press. */
  open(x: number, y: number): void;
  setTimer(fn: () => void, ms: number): unknown;
  clearTimer(handle: unknown): void;
}

/** What to do with a pointer-up of the right button. */
export type MarkingRelease =
  | { kind: "pass" }
  /** A quick click whose press-time `contextmenu` was held back: hand it on now. */
  | { kind: "replay" }
  /** The held menu's button came up here: run what is under it, or keep the menu open. */
  | { kind: "release"; x: number; y: number };

/**
 * The right-button hold, as a state machine over the pointer events (no DOM):
 * idle -> armed (a right press on the view) -> held (the menu opened, the press
 * still down) -> idle. The host feeds it events and acts on the answers.
 */
export class MarkingGesture {
  phase: "idle" | "armed" | "held" = "idle";
  private origin = { x: 0, y: 0 };
  private timer: unknown = null;
  /** A press-time `contextmenu` held back while armed. */
  private stashed = false;
  /** The `contextmenu` that follows the release of a held menu (Windows fires it on release) is swallowed. */
  private swallowNextMenu = false;

  constructor(private readonly opts: MarkingGestureOptions) {}

  /** A pointer press. `eligible`: a plain right press of a mouse, on the 3D view, with the menu on. */
  down(e: { button: number; x: number; y: number; eligible: boolean }): void {
    this.swallowNextMenu = false;
    if (this.phase === "held") return; // another button while held: ignored
    this.reset();
    if (e.button !== 2 || !e.eligible) return;
    this.phase = "armed";
    this.origin = { x: e.x, y: e.y };
    this.timer = this.opts.setTimer(() => this.hold(), this.opts.holdMs);
  }

  private hold(): void {
    this.timer = null;
    if (this.phase !== "armed") return;
    this.phase = "held";
    this.stashed = false; // it was a hold, not a click: the Scene menu stays shut
    this.opts.open(this.origin.x, this.origin.y);
  }

  /** A pointer move; "swallow" while held, so the camera does not pan under the menu. */
  move(x: number, y: number): "pass" | "swallow" {
    if (this.phase === "held") return "swallow";
    if (this.phase === "armed" && Math.hypot(x - this.origin.x, y - this.origin.y) > this.opts.moveTolerancePx) this.reset(); // a pan
    return "pass";
  }

  /** A pointer release. */
  up(e: { button: number; x: number; y: number }): MarkingRelease {
    if (e.button !== 2) return { kind: "pass" };
    if (this.phase === "held") {
      this.phase = "idle";
      this.swallowNextMenu = true;
      return { kind: "release", x: e.x, y: e.y };
    }
    const replay = this.phase === "armed" && this.stashed;
    this.reset();
    return replay ? { kind: "replay" } : { kind: "pass" };
  }

  /** A `contextmenu` event: "swallow" it (held back, or the end of a hold), else "pass" it on to the Scene menu. */
  contextMenu(): "pass" | "swallow" {
    if (this.phase === "armed") {
      this.stashed = true;
      return "swallow";
    }
    if (this.phase === "held") return "swallow";
    if (this.swallowNextMenu) {
      this.swallowNextMenu = false;
      return "swallow";
    }
    return "pass";
  }

  /** Forget the press (the pointer was cancelled, the window lost focus, the menu closed). */
  reset(): void {
    if (this.timer !== null) this.opts.clearTimer(this.timer);
    this.timer = null;
    this.phase = "idle";
    this.stashed = false;
  }
}
