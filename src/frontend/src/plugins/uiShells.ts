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
// selected as the default at BUILD time. Users can still flip back to the base
// UI at runtime (`?ui=core`), which is what makes shipping an alternative UI a
// low-risk operation instead of a fork.
//
// Like `registry.ts`, this module is deliberately free of heavy runtime imports
// (no React runtime, no zustand, no three) so it can be unit-tested under plain
// `node --test` + tsx. React appears `import type` only.

import type React from "react";

/** The built-in shell id — the stock adapy viewer UI. Always registered. */
export const CORE_UI_SHELL_ID = "core";

/** `?ui=<id>` — per-tab override, wins over everything. */
export const UI_SHELL_URL_PARAM = "ui";

/** localStorage key holding the user's sticky choice. */
export const UI_SHELL_STORAGE_KEY = "ada:ui";

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
   */
  load: () => Promise<{ default: React.ComponentType }>;
}

export interface RegisteredUiShell extends UiShellSpec {
  /** Owning plugin id; `"core"` for the built-in shell. */
  pluginId: string;
  order: number;
  builtin: boolean;
}

/** Where the active shell id came from — surfaced for logging + the switcher. */
export type UiShellSource = "url" | "storage" | "build-default" | "builtin";

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
  if (typeof spec.load !== "function") {
    console.warn(`[plugins] UI shell "${id}" has no load(); ignored`);
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
  /** Injectable for tests; defaults to the recorded build default. */
  buildDefault?: string | null;
}

/**
 * Resolve which shell to mount. Precedence, highest first:
 *
 *   1. `?ui=<id>`            — per-tab escape hatch. This is the "always a way
 *                              back to the base UI" guarantee: `?ui=core`.
 *   2. `localStorage ada:ui` — the user's sticky choice from the switcher.
 *   3. build-time default    — `ADA_UI_DEFAULT` baked into the image.
 *   4. `core`                — the built-in UI (or, if core somehow is not
 *                              registered, the first shell by order).
 *
 * A requested-but-unregistered id never blanks the viewer: it is reported in
 * `rejected` and resolution continues down the list.
 */
export function resolveUiShell(opts: ResolveUiShellOptions = {}): UiShellResolution {
  const candidates: Array<{ id: string | null; source: UiShellSource }> = [
    { id: readUrlParam(opts.search), source: "url" },
    { id: opts.stored !== undefined ? opts.stored : readStorage(), source: "storage" },
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

// The active shell is resolved once per page load and cached: swapping shells
// mid-session would mean tearing down the scene, the websocket and every store,
// so a switch is a persist-then-reload (see `setActiveUiShell`).
let _activeCache: UiShellResolution | null = null;

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
  return _activeCache;
}

export function activeUiShellId(): string {
  return activeUiShell().id;
}

/**
 * Persist a shell choice and (by default) reload so it takes effect. Clearing
 * back to the build default is `setActiveUiShell(null)`.
 */
export function setActiveUiShell(id: string | null, opts: { reload?: boolean } = {}): void {
  try {
    if (typeof localStorage !== "undefined") {
      if (id) localStorage.setItem(UI_SHELL_STORAGE_KEY, id);
      else localStorage.removeItem(UI_SHELL_STORAGE_KEY);
    }
  } catch {
    // Storage unavailable — fall through to the URL-param path below, which
    // still gets the user into the shell they picked for this tab.
  }
  _activeCache = null;
  if (opts.reload === false) return;
  try {
    const url = new URL(window.location.href);
    // A stale `?ui=` would outrank the new sticky choice on the next load.
    url.searchParams.delete(UI_SHELL_URL_PARAM);
    window.location.replace(url.toString());
  } catch {
    if (typeof window !== "undefined") window.location.reload();
  }
}
