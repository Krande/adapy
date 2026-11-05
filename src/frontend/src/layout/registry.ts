// The panel registry: one catalogue of every panel the viewer offers, core's and
// plugins' alike, so "which panels exist" has exactly one answer.
//
// Same rules as `@/plugins/registry`: a plain Map, insertion-ordered,
// first-writer-wins on a duplicate id, and no React runtime / zustand / three
// imports, so it runs under plain `node --test`. Core registers its panels from
// `./corePanels`; a plugin's `PanelSlot`s are adapted and registered by
// `registerPlugin` (see `./pluginPanels`).

import type React from "react";

import { activeShellMode } from "./shellModes";

import {
  DEFAULT_PANEL_CAPABILITIES,
  type PanelCapabilities,
  type PanelEnv,
  type PanelId,
  type PanelOwnerId,
  type PanelSpec,
  type RegisteredPanel,
} from "./panel";

const _panels = new Map<PanelId, RegisteredPanel>();

/**
 * What a UI shell changes about a panel it does not own, core's included, without
 * registering a copy: its title, icon, hint, modes and capabilities, and the
 * options its frameless content is given (`SceneBodyProps`, `StorageBodyProps`, ...).
 * The panel keeps its binding, availability and every `reveal()` that names it.
 */
export interface PanelOverride {
  title?: string;
  icon?: React.FC;
  hint?: string;
  shortcut?: string;
  modes?: readonly string[];
  capabilities?: Partial<PanelCapabilities>;
  /**
   * Props for the panel's frameless content, as `PanelProps.options`; or a
   * function of a composed shell's active mode (the Scene panel's tabs per mode, say).
   */
  options?: Readonly<Record<string, unknown>> | ((mode: string | null) => Readonly<Record<string, unknown>>);
}

const _overrides = new Map<PanelId, PanelOverride>();
/** The overridden view of each panel, kept so it is the same object until something changes. */
const _views = new Map<PanelId, { base: RegisteredPanel; mode: string | null; view: RegisteredPanel }>();

/** Install a shell's overrides (replacing any before); null clears them. */
export function setPanelOverrides(map: Readonly<Record<PanelId, PanelOverride>> | null): void {
  _overrides.clear();
  _views.clear();
  for (const [id, o] of Object.entries(map ?? {})) _overrides.set(id, o);
}

function view(p: RegisteredPanel): RegisteredPanel {
  const o = _overrides.get(p.id);
  if (!o) return p;
  const mode = activeShellMode();
  const hit = _views.get(p.id);
  if (hit && hit.base === p && hit.mode === mode) return hit.view;
  const options = typeof o.options === "function" ? o.options(mode) : o.options;
  const v: RegisteredPanel = {
    ...p,
    ...(o.title !== undefined ? { title: o.title } : {}),
    ...(o.icon !== undefined ? { icon: o.icon } : {}),
    ...(o.hint !== undefined ? { hint: o.hint } : {}),
    ...(o.shortcut !== undefined ? { shortcut: o.shortcut } : {}),
    ...(o.modes !== undefined ? { modes: o.modes } : {}),
    capabilities: { ...p.capabilities, ...o.capabilities },
    ...(options !== undefined ? { options: { ...p.options, ...options } } : {}),
  };
  _views.set(p.id, { base: p, mode, view: v });
  return v;
}

/**
 * Register a panel. `ownerId` is `"core"` for core's own panels and the plugin id
 * for a plugin's. A second registration of the same id is ignored with a warning,
 * matching `registerPlugin` and `registerUiShell`.
 */
export function registerPanel(spec: PanelSpec, ownerId: PanelOwnerId): void {
  const id = spec?.id?.trim();
  if (!id) {
    console.warn("[layout] registerPanel called without an id; ignored");
    return;
  }
  if (_panels.has(id)) {
    console.warn(`[layout] duplicate panel id "${id}"; ignoring the later registration`);
    return;
  }
  if (typeof spec.body !== "function") {
    console.warn(`[layout] panel "${id}" has no body(); ignored`);
    return;
  }
  _panels.set(id, {
    ...spec,
    id,
    owner: ownerId,
    capabilities: { ...DEFAULT_PANEL_CAPABILITIES, ...spec.capabilities },
    frame: spec.frame ?? "own",
  });
}

/** Is the panel offered under `env`. A throwing `available` counts as unavailable. */
export function isPanelAvailable(panel: RegisteredPanel, env: PanelEnv): boolean {
  if (!panel.available) return true;
  try {
    return !!panel.available(env);
  } catch (err) {
    console.warn(`[layout] panel "${panel.id}" available() threw; treated as unavailable`, err);
    return false;
  }
}

/**
 * Registered panels in registration order. With `env`, only those whose
 * `available(env)` holds; without it, every registered panel.
 */
export function listPanels(env?: PanelEnv): RegisteredPanel[] {
  const all = [..._panels.values()].map(view);
  return env ? all.filter((p) => isPanelAvailable(p, env)) : all;
}

export function getPanel(id: PanelId): RegisteredPanel | undefined {
  const p = _panels.get(id);
  return p && view(p);
}

/** Panels the classic top bar shows a toggle for, in top-bar order. */
export function listToolbarPanels(env?: PanelEnv): RegisteredPanel[] {
  return listPanels(env)
    .filter((p) => p.toolbar)
    .sort(
      (a, b) =>
        a.toolbar!.order - b.toolbar!.order ||
        (a.toolbar!.subOrder ?? 0) - (b.toolbar!.subOrder ?? 0) ||
        a.id.localeCompare(b.id),
    );
}

/** Drop every panel an owner registered (a plugin registry reset). */
export function removePanelsOwnedBy(ownerId: PanelOwnerId): void {
  for (const [id, p] of _panels) if (p.owner === ownerId) _panels.delete(id);
}

/** Clear the registry -- unit tests only. */
export function resetPanelRegistry(): void {
  _panels.clear();
  setPanelOverrides(null);
}
