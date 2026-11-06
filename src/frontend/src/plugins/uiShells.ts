// UI-shell registry — the "which whole UI do we mount?" half of the plugin
// system. The feature-plugin slots (`registry.ts`) let a plugin add panels,
// buttons and colour fields INSIDE the core UI. A **UI shell** is the other
// axis: a plugin contributes an entire root UI, and core mounts exactly one of
// them. The stock adapy UI is itself registered here as the built-in shell
// `core`, so it is not a special case — it is the shell that ships in the box.
//
// Why this exists: an alternative viewer UI (a full rewrite of the shell,
// docks, design system) is far too big to live as panels bolted into the
// existing chrome, and too churn-heavy to develop inside this repo. With a
// shell slot it can live in its OWN repository, be cloned into
// `packages/plugins/<name>/` during the image build (the exact mechanism the
// external feature plugins already use — see deploy/Dockerfile.viewer), and be
// selected as the default either at BUILD time or, for an image that carries
// several shells, per DEPLOYMENT at runtime (`window.ADA_UI_DEFAULT`, served
// from /config.js). Users can still flip back to the base UI at runtime
// (`?ui=core`), which is what makes shipping an alternative UI a low-risk
// operation instead of a fork.
//
// Like `registry.ts`, this module is deliberately free of heavy runtime imports
// (no React runtime, no zustand, no three) so it can be unit-tested under plain
// `node --test` + tsx. React appears `import type` only.

import type React from "react";

import type { LayoutDoc } from "@/layout/layout";
import type { PanelOverride } from "@/layout/registry";
import type { GridNode } from "@/layout/splitTree";
import type { ViewportAnchor } from "@/layout/viewportOverlays";
import type { AdaPluginContext, PluginTheme } from "./registry";

/** The built-in shell id — the stock Ada Studio UI. Always registered. */
export const CORE_UI_SHELL_ID = "core";

/** `?ui=<id>` — per-tab override, wins over everything. */
export const UI_SHELL_URL_PARAM = "ui";

/** localStorage key holding the user's sticky choice. */
export const UI_SHELL_STORAGE_KEY = "ada:ui";

/** Where a composed shell's panels start, for a user who has not arranged them yet. */
export interface UiShellLayout {
  /**
   * The split grid around the viewport (`GridNode`, one viewport leaf). Used
   * until the user rearranges anything; after that their own arrangement is
   * kept, per shell. Absent: the viewport alone.
   */
  grid?: GridNode;
  /** Panels opened when the shell loads. Where they show is the user's arrangement. */
  open?: string[];
  /**
   * Where a plugin panel that names a dock (`PanelSlot.dock`) goes the first
   * time it is seen: dock id to the id of a group in `grid`. A dock with no
   * group here (or a group the user has since removed) goes to that edge of the
   * view (`left`, `bottom`, otherwise `right`). `float` and `overlay` docks stay
   * in their region.
   */
  docks?: Record<string, string>;
  /**
   * The shell's modes, each with its own arrangement of panels (its own saved
   * document). A mode's `grid`, `open` and `docks` default to the ones above;
   * plugins' modes (`PluginSpec.modes`) are added after these and start from the
   * ones above. A panel that names `modes` shows only in those. Absent: one
   * arrangement, no modes.
   */
  modes?: UiShellMode[];
  /**
   * The id of the mode used when there is no `?mode=` and none stored from an
   * earlier visit. Absent, or not one of the modes: the first by order.
   */
  defaultMode?: string;
  /**
   * The version of these arrangements. Raise it when the shell ships a new one: a
   * user's saved arrangement made under another version is passed to `migrate`,
   * or without it, starts over from the new grid.
   */
  version?: number;
  /** A saved arrangement made under `from` (undefined: before versions), brought up to date; null starts it over. */
  migrate?: (doc: LayoutDoc, from: number | undefined) => LayoutDoc | null;
}

/** One of a composed shell's modes. */
export interface UiShellMode {
  /** Kebab-case; what a panel's `modes` names. */
  id: string;
  label: string;
  hint?: string;
  icon?: React.FC;
  /** Sort key; plugins' modes sort among these by theirs. Default: the position in the list. */
  order?: number;
  grid?: GridNode;
  open?: string[];
  docks?: Record<string, string>;
  /**
   * A panel drawn over the 3D view while this mode is active (a page that needs
   * the whole view, a converter say); the canvas stays mounted underneath.
   */
  viewportCover?: string;
  /** Core suspends the FEA field colouring while it is active (it paints its own). */
  ownsSceneColor?: boolean;
  /** False: core holds the FEA model undeformed while it is active. Default true. */
  showsDeformation?: boolean;
  /** The mode's own controls, drawn by `ShellModeToolbar` while it is active (in the shell's plugin context). */
  toolbar?: (ctx: AdaPluginContext) => React.ReactNode;
}

/** The chrome core draws around a composed shell's layout. */
export interface UiShellChrome {
  /** Drawn full width above the layout: the shell's own menu bar. Lazy. */
  header?: () => Promise<{ default: React.ComponentType }>;
  /** Drawn full width below the layout: a status bar. Lazy. */
  footer?: () => Promise<{ default: React.ComponentType }>;
  /** Drawn down the left of the layout, between header and footer: a tool rail. Lazy; it sizes itself. */
  rail?: () => Promise<{ default: React.ComponentType }>;
  /**
   * The command palette over every registered command, core's panels and modes
   * included. A core feature in every UI, opened by core's Ctrl+K
   * (`core:command-palette`) and by `openCommandPalette()` (a shell's own
   * button). `keys` adds a chord of the shell's own; `false` turns the palette
   * off while this shell is active. Absent: on, with Ctrl+K.
   */
  commandPalette?: { keys?: string } | false;
  /**
   * HUDs over the 3D view: where core's colour legend goes (the canvas then draws
   * none of its own), and where plugin panels that name the `overlay` dock stack.
   * A shell's own HUDs are added with `registerViewportOverlay`.
   */
  overlays?: { legend?: ViewportAnchor; pluginPanels?: ViewportAnchor };
  /** The corner of the layout core's progress toast sits in. Default bottom-right. */
  toastAnchor?: "bottom-right" | "bottom-left" | "top-right" | "top-left";
  /**
   * Drawn above core's canvas-less pages (`/convert`, `/admin`) in place of
   * nothing: the shell's title bar there, given the page's title and the way back
   * to the viewer. Lazy.
   */
  pageHeader?: () => Promise<{ default: React.ComponentType<{ title: string; backHref: string }> }>;
  /** Core's top bar of panel toggles over the viewport. Default true. */
  toolbar?: boolean;
  /**
   * The grid's groups: extra header actions (`"add"` a panel here, `"collapse"`
   * to a strip of icons, `"pin"` a panel across modes) and panel icons in the tabs.
   */
  groups?: { actions?: ("add" | "collapse" | "pin")[]; tabIcons?: boolean };
}

/** A whole-UI implementation a plugin can contribute. */
export interface UiShellSpec {
  /** Globally unique, kebab-case. User-facing: it is what `?ui=` takes. */
  id: string;
  /** Short name for the shell switcher. */
  label: string;
  /** One line shown as the switcher's tooltip / help text. */
  description?: string;
  /** Sort key in the switcher; ties broken by id. Core is 0. */
  order?: number;
  /**
   * Lazy module loader for the shell's root component, React.lazy-compatible.
   * Lazy so a build that carries several shells only downloads the active one
   * (hosted, code-split build). The embed build inlines every chunk, so there
   * it costs bundle size but no extra request — see frontend.md.
   *
   * Omit it to COMPOSE the shell from core instead: core mounts its own root
   * (routing, sign-in, the viewer provider) and its layout of floating and
   * dockable panels, arranged and dressed by `layout`, `chrome` and
   * `themeTokens`. A shell that gives `load` owns all of that itself.
   */
  load?: () => Promise<{ default: React.ComponentType }>;
  /** A composed shell's starting arrangement. */
  layout?: UiShellLayout;
  /** A composed shell's chrome. */
  chrome?: UiShellChrome;
  /**
   * Changes to panels the shell does not own, core's included, by panel id: title,
   * icon, modes, capabilities, and options for their frameless content (`PanelOverride`).
   * The panels keep their bindings and every `reveal()` that names them.
   */
  panels?: Record<string, PanelOverride>;
  /**
   * CSS custom properties set on the document root while the shell is active
   * (`--ada-*`, or the shell's own). A function of the user's panel theme is
   * applied again whenever the theme changes.
   */
  themeTokens?: Record<string, string> | ((theme: PluginTheme) => Record<string, string>);
  /**
   * Tailwind palette variables redefined inside core's panel bodies only
   * (`{"--color-gray-800": "...", "--color-blue-600": "..."}`), so every core
   * body takes the shell's colours without a copy; a function of the user's panel
   * theme is applied again whenever it changes. The shell's own chrome is untouched.
   */
  bodyPalette?: Record<string, string> | ((theme: PluginTheme) => Record<string, string>);
  /**
   * True: switching to or from this shell reloads the page, even when both
   * sides are composed. For a composed shell that sets up something at load
   * time it cannot take down again. Absent: a composed shell (and core's own
   * UI) is switched in place (`setActiveUiShell`); a shell with `load` is
   * always switched by a reload.
   */
  reloadOnSwitch?: boolean;
}

/** Is `spec` composed from core's layout (no root component of its own). */
export function isComposedShell(spec: Pick<UiShellSpec, "load">): boolean {
  return typeof spec.load !== "function";
}

/**
 * Does `spec` run under core's root, so that a switch between two such shells
 * can happen in place: core's own UI and every composed shell, unless it asks
 * for a reload (`reloadOnSwitch`). A shell with `load` brings a root of its own.
 */
export function switchesInPlace(spec: Pick<UiShellSpec, "id" | "load" | "reloadOnSwitch">): boolean {
  if (spec.reloadOnSwitch) return false;
  return spec.id === CORE_UI_SHELL_ID || isComposedShell(spec);
}

export interface RegisteredUiShell extends UiShellSpec {
  /** Owning plugin id; `"core"` for the built-in shell. */
  pluginId: string;
  order: number;
  builtin: boolean;
}

/** Where the active shell id came from — surfaced for logging + the switcher. */
export type UiShellSource = "url" | "storage" | "runtime-default" | "build-default" | "builtin";

export interface UiShellResolution {
  id: string;
  source: UiShellSource;
  /** Set when a requested id was not registered and we fell back. */
  rejected?: { id: string; source: UiShellSource };
}

// Insertion-ordered, id-keyed. First-writer-wins on a duplicate id, matching
// `registerPlugin`'s rule so the two registries behave the same way.
const _shells = new Map<string, RegisteredUiShell>();

// Set from the generated build-time registry (ADA_UI_DEFAULT / plugins.json
// `defaultUi`). null => the built-in core UI is the default.
let _buildDefault: string | null = null;

/**
 * Register a UI shell. Called by `registerPlugin` for each entry in a plugin's
 * `uiShells`, and once by core for the built-in `core` shell.
 */
export function registerUiShell(spec: UiShellSpec, pluginId: string): void {
  const id = spec.id?.trim();
  if (!id) {
    console.warn("[plugins] registerUiShell called without an id; ignored");
    return;
  }
  if (_shells.has(id)) {
    console.warn(`[plugins] duplicate UI shell id "${id}"; ignoring the later registration`);
    return;
  }
  if (spec.load !== undefined && typeof spec.load !== "function") {
    console.warn(`[plugins] UI shell "${id}" has a load that is not a function; ignored`);
    return;
  }
  _shells.set(id, {
    ...spec,
    id,
    pluginId,
    order: spec.order ?? (id === CORE_UI_SHELL_ID ? 0 : 100),
    builtin: id === CORE_UI_SHELL_ID,
  });
}

/** All registered shells, ordered by `(order, id)`. */
export function listUiShells(): RegisteredUiShell[] {
  return [..._shells.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}

export function getUiShell(id: string): RegisteredUiShell | undefined {
  return _shells.get(id);
}

/** Clear the shell registry — unit tests only. */
export function resetUiShells(): void {
  _shells.clear();
  _buildDefault = null;
  _activeCache = null;
  _bootId = null;
  _loadedBySwitch = null;
  _switchCarries.clear();
  _switchHost = null;
  _activeListeners.clear();
}

/**
 * Record the build-time default shell id. Written by the generated registry
 * (`registry.generated.ts`) from `plugins.json` / `ADA_UI_DEFAULT`, so an image
 * built with an alternative UI overlaid boots into it with no user action —
 * which is the whole point of build-time registration.
 */
export function setBuildDefaultUiShellId(id: string | null | undefined): void {
  _buildDefault = id?.trim() || null;
  _activeCache = null;
  _bootId = null;
}

export function buildDefaultUiShellId(): string | null {
  return _buildDefault;
}

function readUrlParam(search?: string): string | null {
  try {
    const raw = search ?? (typeof window !== "undefined" ? window.location?.search : "");
    if (!raw) return null;
    return new URLSearchParams(raw).get(UI_SHELL_URL_PARAM)?.trim() || null;
  } catch {
    return null;
  }
}

/**
 * The server-supplied default, injected as `window.ADA_UI_DEFAULT` by
 * `/config.js`. That script is a blocking, non-deferred classic script in
 * index.html while the SPA entry is a deferred module, so the global is
 * guaranteed to be set before any of this module runs — there is no async
 * ordering problem to design around here.
 *
 * Absent (dev server, embed bundle, or a deployment that configures nothing)
 * means *no runtime opinion*, and the build-time default below still applies.
 * That is what keeps existing images behaving exactly as they did.
 */
function readRuntimeDefault(): string | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = (window as { ADA_UI_DEFAULT?: unknown }).ADA_UI_DEFAULT;
    return typeof raw === "string" ? raw.trim() || null : null;
  } catch {
    return null;
  }
}

/** The runtime default shell id, or null when the server configured none. */
export function runtimeDefaultUiShellId(): string | null {
  return readRuntimeDefault();
}

function readStorage(): string | null {
  try {
    if (typeof localStorage === "undefined") return null;
    return localStorage.getItem(UI_SHELL_STORAGE_KEY)?.trim() || null;
  } catch {
    // Storage disabled (private mode / embed sandbox) => no sticky choice.
    return null;
  }
}

export interface ResolveUiShellOptions {
  /** Injectable for tests; defaults to `window.location.search`. */
  search?: string;
  /** Injectable for tests; defaults to `localStorage[ada:ui]`. */
  stored?: string | null;
  /** Injectable for tests; defaults to `window.ADA_UI_DEFAULT`. */
  runtimeDefault?: string | null;
  /** Injectable for tests; defaults to the recorded build default. */
  buildDefault?: string | null;
}

/**
 * Resolve which shell to mount. Precedence, highest first:
 *
 *   1. `?ui=<id>`            — per-tab escape hatch. This is the "always a way
 *                              back to the base UI" guarantee: `?ui=core`.
 *   2. `localStorage ada:ui` — the user's sticky choice from the switcher.
 *   3. runtime default       — `window.ADA_UI_DEFAULT` from /config.js, i.e.
 *                              what THIS deployment configured. Above the
 *                              build-time value so one image can serve several
 *                              deployments with different UIs, and so changing
 *                              the UI (or rolling one back) is a config edit
 *                              and a restart rather than an image rebuild.
 *                              Below the two user-driven sources so a
 *                              deployment can never take `?ui=core` away.
 *   4. build-time default    — `ADA_UI_DEFAULT` baked into the image.
 *   5. `core`                — the built-in UI (or, if core somehow is not
 *                              registered, the first shell by order).
 *
 * A requested-but-unregistered id never blanks the viewer: it is reported in
 * `rejected` and resolution continues down the list. That matters most for the
 * runtime value: a typo there reaches a running deployment, where the same typo
 * in the build-time value would have been caught at build time. The worst case
 * is the next source down — ultimately the built-in UI — never a blank page.
 */
export function resolveUiShell(opts: ResolveUiShellOptions = {}): UiShellResolution {
  const candidates: Array<{ id: string | null; source: UiShellSource }> = [
    { id: readUrlParam(opts.search), source: "url" },
    { id: opts.stored !== undefined ? opts.stored : readStorage(), source: "storage" },
    {
      id: opts.runtimeDefault !== undefined ? opts.runtimeDefault : readRuntimeDefault(),
      source: "runtime-default",
    },
    { id: opts.buildDefault !== undefined ? opts.buildDefault : _buildDefault, source: "build-default" },
  ];

  let rejected: UiShellResolution["rejected"];
  for (const c of candidates) {
    if (!c.id) continue;
    if (_shells.has(c.id)) return { id: c.id, source: c.source, rejected };
    // Remember only the first rejection — it is the one the user asked for.
    if (!rejected) rejected = { id: c.id, source: c.source };
  }

  if (_shells.has(CORE_UI_SHELL_ID)) return { id: CORE_UI_SHELL_ID, source: "builtin", rejected };
  const first = listUiShells()[0];
  return { id: first?.id ?? CORE_UI_SHELL_ID, source: "builtin", rejected };
}

// The active shell is resolved once per page load and cached. It changes during
// the page's life only through an in-place switch (`setActiveUiShell`), which
// tells the subscribers below.
let _activeCache: UiShellResolution | null = null;
// The shell the page booted in: what the multi-window dashboard is keyed by
// (`layout/windows`), which an in-place switch does not move.
let _bootId: string | null = null;

export function activeUiShell(): UiShellResolution {
  if (!_activeCache) {
    _activeCache = resolveUiShell();
    if (_activeCache.rejected) {
      console.warn(
        `[plugins] UI shell "${_activeCache.rejected.id}" (from ${_activeCache.rejected.source}) ` +
          `is not registered in this build; falling back to "${_activeCache.id}"`,
      );
    }
  }
  if (_bootId === null) {
    _bootId = _activeCache.id;
    // Read the switch marker at boot, so it never outlives this page.
    loadedByShellSwitch();
  }
  return _activeCache;
}

export function activeUiShellId(): string {
  return activeUiShell().id;
}

/** The shell this page booted in. Differs from `activeUiShellId()` after an in-place switch. */
export function bootUiShellId(): string {
  return _bootId ?? activeUiShellId();
}

const _activeListeners = new Set<() => void>();
let _activeVersion = 0;

/** Called after an in-place switch has changed the active shell. Returns the unsubscribe. */
export function subscribeActiveUiShell(cb: () => void): () => void {
  _activeListeners.add(cb);
  return () => {
    _activeListeners.delete(cb);
  };
}

/** Changes on every in-place switch (for `useSyncExternalStore`). */
export function activeUiShellVersion(): number {
  return _activeVersion;
}

/**
 * Can a switch from shell `from` to shell `to` happen in place: both registered
 * and both run under core's root (`switchesInPlace`).
 */
export function canSwitchInPlace(from: string, to: string): boolean {
  const a = _shells.get(from);
  const b = _shells.get(to);
  return !!a && !!b && switchesInPlace(a) && switchesInPlace(b);
}

/**
 * Who takes a switch in place: `UiShellHost`, while it mounts core's root. Asked
 * with the shell switched to; true means it will show that shell once the
 * active one changes, false that the page must reload.
 */
export type UiShellSwitchHost = (to: string) => boolean;

let _switchHost: UiShellSwitchHost | null = null;

/** Install the in-place switch host (null: none, every switch reloads). Returns the undo. */
export function setUiShellSwitchHost(host: UiShellSwitchHost | null): () => void {
  _switchHost = host;
  return () => {
    if (_switchHost === host) _switchHost = null;
  };
}

/**
 * Query params a shell switch should carry into its reload, or null for none.
 *
 * A switch that cannot happen in place reloads the page, and the page it
 * reloads is whatever the address bar shows. Loads that arrive through the URL
 * consume their params and strip them (`useUrlParamLoad`: `?scope&file&derived`
 * and any plugin params with them), so after a deep-linked load the address
 * bar no longer says what is on screen and a switch would boot the other shell
 * with an empty scene. The code that strips params registers a carry here so
 * the switch can put them back.
 */
export type ShellSwitchCarry = () => Record<string, string> | null | undefined;

const _switchCarries = new Set<ShellSwitchCarry>();

/**
 * Register a provider of params for the next reloading shell switch's URL.
 * Asked at the moment of the switch, so it can answer from what is loaded
 * then. Returns a function that removes it.
 */
export function carryOnShellSwitch(carry: ShellSwitchCarry): () => void {
  _switchCarries.add(carry);
  return () => {
    _switchCarries.delete(carry);
  };
}

/**
 * The URL a reloading shell switch goes to: `href` without `?ui=` (a stale one
 * would outrank the new sticky choice), plus every carried param the URL does
 * not already hold. A param in the URL wins over a carried one, and `ui` is
 * never carried. A throwing provider is skipped, never the switch.
 */
export function shellSwitchUrl(href: string): string {
  const url = new URL(href);
  url.searchParams.delete(UI_SHELL_URL_PARAM);
  for (const carry of _switchCarries) {
    let params: Record<string, string> | null | undefined;
    try {
      params = carry();
    } catch (err) {
      console.warn("[plugins] a shell-switch carry threw; its params are not carried", err);
      continue;
    }
    if (!params) continue;
    for (const [key, value] of Object.entries(params)) {
      if (key === UI_SHELL_URL_PARAM || url.searchParams.has(key)) continue;
      url.searchParams.set(key, value);
    }
  }
  return url.toString();
}

/** sessionStorage key set just before a switch reloads: the next page is that switch's. */
export const UI_SHELL_SWITCH_RELOAD_KEY = "ada:ui-switch-reload";

let _loadedBySwitch: boolean | null = null;

/**
 * Was this page loaded by a reloading shell switch? Read once a page (the
 * marker is removed as it is read, so a manual reload after it is a plain
 * reload again). The scene restore asks, to bring the previous page's models
 * back even with "restore after reload" off.
 */
export function loadedByShellSwitch(): boolean {
  if (_loadedBySwitch !== null) return _loadedBySwitch;
  _loadedBySwitch = false;
  try {
    if (typeof sessionStorage === "undefined") return false;
    _loadedBySwitch = sessionStorage.getItem(UI_SHELL_SWITCH_RELOAD_KEY) === "1";
    if (_loadedBySwitch) sessionStorage.removeItem(UI_SHELL_SWITCH_RELOAD_KEY);
  } catch {
    // No session storage: not a switch's page as far as anyone can tell.
  }
  return _loadedBySwitch;
}

/** Drop `?ui=` from the address bar without navigating: it would outrank the choice on a manual reload. */
function stripUiParamInPlace(): void {
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(UI_SHELL_URL_PARAM)) return;
    url.searchParams.delete(UI_SHELL_URL_PARAM);
    window.history.replaceState(window.history.state, "", url.toString());
  } catch {
    // No history API (a test runtime): nothing in an address bar to fix.
  }
}

/**
 * Persist a shell choice and make it take effect. Clearing back to the
 * deployment's default is `setActiveUiShell(null)`.
 *
 * Between two shells that run under core's root (core's own UI and composed
 * shells, `switchesInPlace`) the switch happens IN PLACE when `UiShellHost` is
 * mounted: the viewer, its 3D canvas, the scene and every store stay, and only
 * the shell's layout, chrome, modes, panel overrides and look change. Otherwise
 * (a shell with a root of its own, one that sets `reloadOnSwitch`) the page
 * reloads into the new shell, carrying what it can: see `shellSwitchUrl` and
 * `loadedByShellSwitch`. `reload: false` only persists the choice; the
 * next page load picks it up.
 */
export function setActiveUiShell(id: string | null, opts: { reload?: boolean } = {}): void {
  try {
    if (typeof localStorage !== "undefined") {
      if (id) localStorage.setItem(UI_SHELL_STORAGE_KEY, id);
      else localStorage.removeItem(UI_SHELL_STORAGE_KEY);
    }
  } catch {
    // Storage unavailable: an in-place switch still happens; a reload carries
    // nothing that would choose the shell, and boots the default.
  }
  _activeCache = null;
  if (opts.reload === false) return;

  // The shell switched to, as the next page would resolve it without `?ui=`.
  const next: UiShellResolution =
    id && _shells.has(id) ? { id, source: "storage" } : resolveUiShell({ search: "", stored: id });
  let inPlace = false;
  try {
    inPlace = !!_switchHost && _switchHost(next.id);
  } catch (err) {
    console.warn("[plugins] the in-place shell switch failed; reloading instead", err);
  }
  if (inPlace) {
    _activeCache = { id: next.id, source: next.source };
    stripUiParamInPlace();
    _activeVersion++;
    for (const l of [..._activeListeners]) {
      try {
        l();
      } catch (err) {
        console.warn("[plugins] a UI-shell listener threw", err);
      }
    }
    return;
  }

  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(UI_SHELL_SWITCH_RELOAD_KEY, "1");
  } catch {
    // No session storage: the scene restore follows its own setting.
  }
  try {
    window.location.replace(shellSwitchUrl(window.location.href));
  } catch {
    if (typeof window !== "undefined") window.location.reload();
  }
}
