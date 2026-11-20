// The modes of the UI on screen: whole activities (`Mode` in `./modes`, or a plugin's), each with
// its own arrangement of panels and its own behaviour. A composed shell declares the ones it offers
// in `UiShellLayout.modes`; plugins add theirs (`PluginSpec.modes`). Core's own UI has none, and then
// nothing here is set.
//
// The state is one zustand store (`modeStore`): the modes on offer, the active one, the switcher's
// badges and the UI's switching rules. The functions below are its API, and what the facade root
// exports; the React side is `./ShellModeSwitcher` (`useShellMode`).

import type React from "react";
import { createStore } from "zustand/vanilla";

import type { AdaPluginContext } from "@/plugins/registry";

import type { BuilderPresence, ModeBehaviour, ModeId, ModeSwitching } from "./modes";

export interface ShellMode {
  id: ModeId;
  label: string;
  hint?: string;
  icon?: React.FC;
  order: number;
  /** The heading it sits under (`ModeGroup`), when it has one. */
  group?: string;
  /** The plugin that contributed it; absent for the shell's own. */
  pluginId?: string;
  /** What the mode does, as the UI declared it (`UiShellMode.behaviour`). */
  behaviour?: ModeBehaviour;
  /** Core suspends the FEA field colouring while it is active (`PluginModeSpec.ownsSceneColor`). */
  ownsSceneColor?: boolean;
  /** False: core holds the FEA model undeformed while it is active. Default true. */
  showsDeformation?: boolean;
  /** The mode's own controls (`PluginModeSpec.toolbar`), run as `pluginId`. */
  toolbar?: (ctx: AdaPluginContext) => React.ReactNode;
  /** A shell's own controls for a plugin's mode (`modeArrangements[id].toolbar`), drawn after the
   *  mode's: run as the shell (`shellPluginId`), never as the mode's plugin, and an error in them
   *  is the shell's. */
  shellToolbar?: (ctx: AdaPluginContext) => React.ReactNode;
  /** The shell plugin `shellToolbar` and `shellToolbarEnd` run as. */
  shellPluginId?: string;
  /** A shell's controls drawn at the far right of the mode's toolbar region (`UiShellMode.toolbarEnd`,
   *  `modeArrangements[id].toolbarEnd`), outside its clip; run as the shell (`shellPluginId`). */
  shellToolbarEnd?: (ctx: AdaPluginContext) => React.ReactNode;
  /** Core toolbars drawn for the mode (`UiShellMode.toolbars`), before its own `toolbar`. */
  toolbars?: readonly string[];
  /** Offered only while this holds (`UiShellMode.requires`; `./modeRequirements`). */
  requires?: "builder";
  /** The panels the mode opens at the start (`UiShellMode.open`). */
  open?: readonly string[];
}

/** Core's own mode toolbars (`./ModeToolbars`), by the id a mode's `toolbars` names. */
export const CORE_MODE_TOOLBAR_IDS = ["playback", "section", "representation", "colorBy", "build"] as const;
export type CoreModeToolbarId = (typeof CORE_MODE_TOOLBAR_IDS)[number];

/** A mode's badge in the switcher: a count, a dot, or none. */
export type ShellModeBadge = number | "dot";

export interface ModeState {
  /** The modes on offer, ordered by `(order, id)`. Empty in core's own UI. */
  modes: readonly ShellMode[];
  /** The active mode's id; null without modes. */
  active: ModeId | null;
  badges: Readonly<Record<string, ShellModeBadge>>;
  /** How the UI on screen switches modes (`UiShellLayout.modeSwitching`). */
  switching: Readonly<ModeSwitching>;
  /** Bumped on every change, for `useSyncExternalStore`. */
  version: number;
}

/** The modes of the UI on screen. Prefer the functions below; this is for a zustand selector. */
export const modeStore = createStore<ModeState>()(() => ({ modes: [], active: null, badges: {}, switching: {}, version: 0 }));

const state = () => modeStore.getState();
const update = (patch: Partial<Omit<ModeState, "version">>) => modeStore.setState((s) => ({ ...patch, version: s.version + 1 }));

/** Install a shell's modes, ordered by `(order, id)`; `initial` if it is one of them, else the first. */
export function setShellModes(list: readonly ShellMode[], initial?: string | null): void {
  const modes = [...list].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  update({ modes, active: modes.find((m) => m.id === initial)?.id ?? modes[0]?.id ?? null });
}

export function listShellModes(): readonly ShellMode[] {
  return state().modes;
}

/** The active mode's id; null when the shell has no modes. */
export function activeShellMode(): ModeId | null {
  return state().active;
}

/** The active mode; null when the shell has no modes. */
export function activeModeDef(): ShellMode | null {
  const { modes, active } = state();
  return modes.find((m) => m.id === active) ?? null;
}

/** Does the active mode open panel `id` at the start (its `open` list)? */
export function activeModeOpens(id: string): boolean {
  return !!activeModeDef()?.open?.includes(id);
}

/** Switch mode. An id the shell does not have is ignored (and returns false). */
export function setShellMode(id: ModeId): boolean {
  const { modes, active } = state();
  if (!modes.some((m) => m.id === id)) return false;
  if (active !== id) update({ active: id });
  return true;
}

/**
 * The mode a shell opens in: `?mode=`, else the one used last, else the shell's
 * `layout.defaultMode`, else the first. An id that is not one of `modes` is passed over; an id
 * the shell used before (`switching.aliases`) is read as the mode it is now. `switching` may turn
 * off the link (`urlParam: false`) and the memory (`remember: false`).
 */
export function initialShellMode(
  modes: readonly Pick<ShellMode, "id">[],
  asked: string | null,
  stored: string | null,
  fallback?: string,
  switching: ModeSwitching = {},
): string | null {
  const resolve = (id: string | null | undefined) => (id ? (switching.aliases?.[id] ?? id) : id);
  const has = (id: string | null | undefined): id is string => !!id && modes.some((m) => m.id === id);
  const candidates = [switching.urlParam === false ? null : asked, switching.remember === false ? null : stored, fallback];
  return candidates.map(resolve).find(has) ?? null;
}

/** Record how the UI on screen switches modes (`UiShellLayout.modeSwitching`); empty for none. */
export function setModeSwitching(switching: ModeSwitching | undefined): void {
  update({ switching: switching ?? {} });
}

/** How the UI on screen switches modes. */
export function modeSwitching(): Readonly<ModeSwitching> {
  return state().switching;
}

/**
 * What the cell builder is in the active mode (`BuilderPresence`): the mode's declared
 * `behaviour.builder`; `"compare"` for a mode that declares none; `"edit"` without modes (core's
 * own UI), where nothing about a mode applies.
 */
export function builderPresence(): BuilderPresence {
  const mode = activeModeDef();
  if (!mode) return "edit";
  return mode.behaviour?.builder ?? "compare";
}

/** Does the active mode hold the FEA field colouring aside (`behaviour.sceneColor`, else `ownsSceneColor`). */
export function modeOwnsSceneColor(mode: ShellMode | null): boolean {
  if (!mode) return false;
  const declared = mode.behaviour?.sceneColor;
  return declared ? declared === "own" : !!mode.ownsSceneColor;
}

/** Does `mode` draw the FEA model deformed (`behaviour.deformation`, else `showsDeformation`, default true). */
export function modeShowsDeformation(mode: ShellMode | null): boolean {
  if (!mode) return true;
  return mode.behaviour?.deformation ?? mode.showsDeformation !== false;
}

/**
 * Mark a mode in the switcher: a count of things waiting there, a dot, or (null)
 * nothing. For a mode that is not the active one, so the user sees work elsewhere.
 */
export function setShellModeBadge(id: ModeId, badge: ShellModeBadge | null): void {
  const { badges } = state();
  if ((badges[id] ?? null) === badge) return;
  const next = { ...badges };
  if (badge === null || badge === 0) delete next[id];
  else next[id] = badge;
  update({ badges: next });
}

export function shellModeBadge(id: ModeId): ShellModeBadge | null {
  return state().badges[id] ?? null;
}

/** The URL parameter that opens a shell in a mode (`?mode=fem-results`). */
export const SHELL_MODE_PARAM = "mode";

export function subscribeShellModes(cb: () => void): () => void {
  return modeStore.subscribe(() => cb());
}

/** For `useSyncExternalStore`: changes whenever the modes, the active one, a badge or the rules do. */
export function shellModesVersion(): number {
  return state().version;
}

/** Is `panel` offered in the active mode: it names no modes, or names this one. */
export function offeredInMode(panel: { modes?: readonly string[] }): boolean {
  const { active } = state();
  return !panel.modes || active === null || panel.modes.includes(active);
}
