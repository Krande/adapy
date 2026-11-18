// The composed shell the classic preset serves, and the documents it reads and
// writes: one per mode (context `<shell>` or `<shell>/<mode>`), core's own UI
// under `"default"`. Also the shell's chrome (group actions, HUD anchors, the
// toast corner), its pinned panels and its saved workspaces. No JSX: what a
// panel's header shows and how it moves stays in `./classicPlace`.

import {useMemo, useSyncExternalStore} from "react";
import {useStore} from "zustand";

import {CORE_PANEL_OWNER} from "../corePanelIds";
import {setCustomisableShell} from "../customisable";
import {CLASSIC_PRESET_ID, layoutDocKey, type LayoutDoc} from "../layout";
import {layoutStore} from "../layoutStore";
import {reveal} from "../reveal";
import type {PanelId} from "../panel";
import {emptyLayoutDoc, isInGrid, migrateLayoutDoc} from "../reducers";
import {activeShellMode, shellModesVersion, subscribeShellModes} from "../shellModes";
import type {GridNode} from "../splitTree";
import {setUiFreezeShell} from "../uiFreeze";
import type {ViewportAnchor} from "../viewportOverlays";
import {CLASSIC_ZONE_DEFS} from "./classicRules";

/** One arrangement of a composed shell: its starting grid, panels to open, dock groups. */
export interface ClassicArrangement {
  grid?: GridNode;
  open?: readonly PanelId[];
  docks?: Readonly<Record<string, string>>;
  /** A panel drawn over the 3D view (the canvas stays mounted under it). */
  cover?: PanelId;
}

/** Extra actions a composed shell gives each group of the grid. */
export type GroupActionId = "collapse" | "pin" | "add";

/** A composed shell as the classic frame serves it: its id, its arrangement, and per-mode overrides. */
export interface ClassicShell extends ClassicArrangement {
  id: string;
  /** By mode id. A mode missing here (a plugin's) uses the shell's arrangement. */
  modes?: Readonly<Record<string, ClassicArrangement>>;
  /** Group chrome: extra header actions, and panel icons in the tabs. */
  groups?: {actions?: readonly GroupActionId[]; tabIcons?: boolean};
  /** HUDs over the view: core's legend and the `overlay`-dock plugin panels, by anchor. */
  overlays?: {legend?: ViewportAnchor; pluginPanels?: ViewportAnchor};
  /** The corner of the layout the progress toast sits in. */
  toastAnchor?: "bottom-right" | "bottom-left" | "top-right" | "top-left";
  /** The arrangements' version, and how a saved one of another version is brought up to date. */
  version?: number;
  /** Customisable panels: always on ("required", the default), or the user's setting for this shell. */
  customisablePanels?: "required" | "optional";
  /** UI freeze (`./uiFreeze`): `false` leaves it out, `"default-on"` starts frozen. Default offered, off. */
  uiFreeze?: boolean | "default-on";
  migrate?: (doc: LayoutDoc, from: number | undefined) => LayoutDoc | null;
  /** One panel for every mode (`UiShellLayout.globalPanels`). */
  globalPanels?: readonly PanelId[];
}

/** The group chrome the served shell asks for; none for core's own UI. */
export function classicGroupChrome(): {actions: readonly GroupActionId[]; tabIcons: boolean} {
  return {actions: shell?.groups?.actions ?? [], tabIcons: !!shell?.groups?.tabIcons};
}

/** Where the served shell puts HUDs over the view; none for core's own UI. */
export function classicOverlays(): {legend?: ViewportAnchor; pluginPanels?: ViewportAnchor} | null {
  return shell?.overlays ?? null;
}

/** The corner the served shell puts the progress toast in; null for the default. */
export function classicToastAnchor(): ClassicShell["toastAnchor"] | null {
  return shell?.toastAnchor ?? null;
}

/** The served shell's pinned panels. */
export function pinnedPanels(): readonly PanelId[] {
  return shell ? (layoutStore.getState().prefs.pinned?.[shell.id] ?? []) : [];
}

/** The served shell's pinned panels, re-rendering on change. */
export function usePinnedPanels(): readonly PanelId[] {
  const id = shell?.id;
  return useStore(layoutStore, (s) => (id ? s.prefs.pinned?.[id] : undefined)) ?? NONE;
}
const NONE: readonly PanelId[] = [];

export function togglePin(id: PanelId): void {
  if (shell) layoutStore.getState().setPinned(shell.id, id, !pinnedPanels().includes(id));
}

/** The composed shell served; null for core's own UI. Set before the frame renders, again by an in-place shell switch. */
let shell: ClassicShell | null = null;

/** The composed shell served, as set; null for core's own UI. */
export function servedClassicShell(): ClassicShell | null {
  return shell;
}

/** The id of the composed shell served; null for core's own UI. */
export function classicShellId(): string | null {
  return shell?.id ?? null;
}

/**
 * Serve a composed shell: a document of its own per mode (context `<shell>` or
 * `<shell>/<mode>`), each starting from its arrangement's grid until the user
 * rearranges. `modeIds` are every mode the shell will switch to, plugins'
 * included. `null` serves core's own UI again.
 */
export function setClassicShell(next: ClassicShell | null, modeIds: readonly string[] = []): void {
  shell = next;
  shellContexts = ["default"];
  setCustomisableShell(next?.id ?? null, next?.customisablePanels !== "optional");
  setUiFreezeShell(next?.id ?? null, next?.uiFreeze);
  if (!next) return;
  const contexts = modeIds.length ? modeIds.map((m) => [contextOf(next.id, m), m] as const) : [[next.id, null] as const];
  shellContexts = contexts.map(([ctx]) => ctx);
  for (const [ctx, mode] of contexts) {
    const grid = arrangementOf(mode).grid;
    const version = next.version !== undefined ? {shellVersion: next.version} : {};
    layoutStore
      .getState()
      .setStartingDoc(CLASSIC_PRESET_ID, ctx, grid || next.version !== undefined ? () => ({...emptyLayoutDoc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx), ...(grid ? {grid} : {}), ...version}) : null);
  }
}

const contextOf = (shellId: string, mode: string | null) => (mode ? `${shellId}/${mode}` : shellId);

/** Every document context of the served UI: one per mode of a composed shell, else core's one. */
let shellContexts: string[] = ["default"];
const owner = () => shell?.id ?? CORE_PANEL_OWNER;
const docKey = (ctx: string) => layoutDocKey(CLASSIC_PRESET_ID, ctx);

/**
 * Back to the starting arrangement (the shell's grid for the mode): of the
 * active mode, or with `all`, of every mode. Panels stay open; where they show
 * starts over, and the active mode's starting panels open again (one closed since
 * was remembered as closed with the arrangement just dropped). Another mode opens
 * its starting list when it is next entered, its arrangement being new.
 */
export function resetArrangement(all = false): void {
  for (const ctx of all ? shellContexts : [classicContext()]) layoutStore.getState().removeDoc(CLASSIC_PRESET_ID, ctx);
  for (const id of arrangementOf().open ?? []) reveal(id);
}

/**
 * Bring the saved arrangements up to the shell's `version`: one made under
 * another goes through the shell's `migrate`, or starts over without it. Call
 * once the saved prefs are loaded.
 */
export function upgradeShellDocs(): void {
  const version = shell?.version;
  if (!shell || version === undefined) return;
  const store = layoutStore.getState();
  const docs: Record<string, LayoutDoc> = {};
  for (const ctx of shellContexts) {
    const raw = store.prefs.docs[docKey(ctx)];
    if (!raw || raw.shellVersion === version) continue;
    let next: LayoutDoc | null = null;
    try {
      next = shell.migrate ? shell.migrate(migrateLayoutDoc(raw, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx), raw.shellVersion) : null;
    } catch (err) {
      console.warn(`[layout] the shell's migrate threw for "${ctx}"; starting it over`, err);
    }
    if (next) docs[docKey(ctx)] = {...migrateLayoutDoc(next, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx), shellVersion: version};
    else store.removeDoc(CLASSIC_PRESET_ID, ctx);
  }
  if (Object.keys(docs).length) store.putDocs(docs);
}

/** Save every mode's arrangement under `name` (replacing one of that name). */
export function saveWorkspace(name: string): void {
  const docs = layoutStore.getState().prefs.docs;
  const saved: Record<string, LayoutDoc> = {};
  for (const ctx of shellContexts) {
    const d = docs[docKey(ctx)];
    if (d) saved[docKey(ctx)] = d;
  }
  layoutStore.getState().setWorkspace(owner(), name.trim(), saved);
}

/** Put a saved workspace's arrangements in place. False when there is none of that name. */
export function loadWorkspace(name: string): boolean {
  const ws = layoutStore.getState().prefs.workspaces?.[owner()]?.[name];
  if (!ws) return false;
  const docs: Record<string, LayoutDoc> = {};
  for (const ctx of shellContexts) {
    const d = ws[docKey(ctx)];
    if (d) docs[docKey(ctx)] = migrateLayoutDoc(d, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx);
  }
  layoutStore.getState().putDocs(docs);
  return true;
}

export function deleteWorkspace(name: string): void {
  layoutStore.getState().setWorkspace(owner(), name, null);
}

export function listWorkspaces(): string[] {
  return Object.keys(layoutStore.getState().prefs.workspaces?.[owner()] ?? {}).sort((a, b) => a.localeCompare(b));
}

/** The arrangement of `mode` (the active one by default): the mode's own over the shell's. */
export function arrangementOf(mode: string | null = activeShellMode()): ClassicArrangement {
  if (!shell) return {};
  const own = (mode && shell.modes?.[mode]) || {};
  return {grid: own.grid ?? shell.grid, open: own.open ?? shell.open, docks: own.docks ?? shell.docks, cover: own.cover};
}

/** The panel the active mode draws over the 3D view, if any. */
export function classicCover(): PanelId | null {
  return arrangementOf().cover ?? null;
}

/** The document context the classic frame reads and writes: `"default"` for core's own UI. */
export function classicContext(): string {
  return shell ? contextOf(shell.id, activeShellMode()) : "default";
}

/** The context, re-rendering when the shell's mode changes. */
export function useClassicContext(): string {
  useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  return classicContext();
}


/** Core's top bar of toggles; a composed shell with a menu bar of its own may leave it out. */
let toolbarShown = true;

export function setClassicToolbar(on: boolean): void {
  toolbarShown = on;
}

export function classicToolbarShown(): boolean {
  return toolbarShown;
}

/** The user's classic document (of the active mode), re-rendering when it or the mode changes. */
export function useClassicDoc(): LayoutDoc {
  const ctx = useClassicContext();
  const raw = useStore(layoutStore, (s) => s.prefs.docs[layoutDocKey(CLASSIC_PRESET_ID, ctx)]);
  // Read through the store, so an unsaved document starts from the shell's grid.
  return useMemo(() => (raw ? migrateLayoutDoc(raw, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, ctx) : classicDoc()), [raw, ctx]);
}

export function classicDoc(): LayoutDoc {
  return layoutStore.getState().doc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, classicContext());
}

export function updateClassic(fn: (doc: LayoutDoc) => LayoutDoc): void {
  layoutStore.getState().update(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, fn, classicContext());
}


/** Where a panel is in a document: its home, a window of the layout, or the grid. */
export type Place = "home" | "float" | "grid";

export function placeOf(doc: LayoutDoc, id: PanelId): Place {
  if (isInGrid(doc, id)) return "grid";
  if (doc.floats[id]) return "float";
  return "home";
}

