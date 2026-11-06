// A UI shell composed from core (`UiShellSpec` without `load`): core's layout of
// floating and dockable panels, in documents of the shell's own (one per mode),
// starting from the shell's grids, under the shell's header and theme tokens.
// `AppRoot` (in `@/app`) wraps it in the routes, the sign-in gate and the viewer
// provider that every shell shares. Core's own UI is this layout with no shell
// (`spec` null), so a switch between it and a composed shell, or between two
// composed shells, only hands in another `spec` (see `UiShellHost`).

import React, {Suspense, useEffect, useRef} from "react";

import {getPluginModes} from "@/plugins/registry";
import type {RegisteredUiShell, UiShellSpec} from "@/plugins/uiShells";
import {setFeaDeformationShown} from "@/utils/scene/fea/deformationGate";
import {notifyActiveModeSceneColor} from "@/utils/scene/fea/modeSceneColor";

import {useShellLook} from "./shellTheme";
import {commandsOwnedBy, registerCommand, unregisterCommand, type RegisteredCommand} from "./commands";
import {CORE_PANEL_OWNER} from "./corePanelIds";
import {registerShortcut, shortcutsOwnedBy, unregisterShortcut, type RegisteredShortcut} from "./shortcuts";
import {registerViewportOverlay, unregisterViewportOverlay, viewportOverlaysOwnedBy, type ViewportOverlaySpec} from "./viewportOverlays";
import {registerCoreCommands} from "./coreCommands";
import {ShortcutsDialogHost} from "@/components/options/ShortcutsModal";
import {CoreDialogsHost} from "./CoreDialogsHost";
import LayoutRoot from "./LayoutRoot";
import {setPanelOverrides} from "./registry";
import {setClassicShell, setClassicToolbar, type ClassicArrangement} from "./presets/classicPlace";
import {SHELL_MODE_PARAM, activeShellMode, listShellModes, setShellModes, subscribeShellModes, type ShellMode} from "./shellModes";

/** Who holds the FEA model undeformed for a mode that shows no deformation. */
const MODE_DEFORMATION_OWNER = "shell-mode";

/** Where the active mode is remembered, per shell. */
const modeKey = (shellId: string) => `ada:shell-mode:${shellId}`;

function readMode(shellId: string): string | null {
  try {
    return localStorage.getItem(modeKey(shellId));
  } catch {
    return null;
  }
}

/** The shell's modes, then the plugins' (only when the shell has modes at all). */
export function shellModes(spec: UiShellSpec): ShellMode[] {
  const own = spec.layout?.modes ?? [];
  if (!own.length) return [];
  // The shell's own modes' toolbars run in the shell plugin's context.
  const shellPlugin = (spec as Partial<RegisteredUiShell>).pluginId;
  const mine: ShellMode[] = own.map((m, i) => ({
    id: m.id,
    label: m.label,
    hint: m.hint,
    icon: m.icon,
    order: m.order ?? i,
    pluginId: m.toolbar ? shellPlugin : undefined,
    ownsSceneColor: m.ownsSceneColor,
    showsDeformation: m.showsDeformation,
    toolbar: m.toolbar,
  }));
  const theirs: ShellMode[] = getPluginModes()
    .filter(({mode}) => !own.some((m) => m.id === mode.id))
    .map(({pluginId, mode}) => ({
      id: mode.id,
      label: mode.label,
      hint: mode.hint,
      icon: mode.icon,
      order: mode.order ?? 100,
      pluginId,
      ownsSceneColor: mode.ownsSceneColor,
      // A plugin's mode opts in to the deformed model; the shell's own modes keep the default (shown).
      showsDeformation: mode.showsDeformation ?? false,
      toolbar: mode.toolbar,
    }));
  return [...mine, ...theirs];
}

/**
 * The mode a shell opens in: `?mode=`, else the one used last, else the shell's
 * `layout.defaultMode`, else the first. An id that is not one of `modes` is passed over.
 */
export function initialShellMode(
  modes: readonly Pick<ShellMode, "id">[],
  asked: string | null,
  stored: string | null,
  fallback?: string,
): string | null {
  const has = (id: string | null | undefined): id is string => !!id && modes.some((m) => m.id === id);
  return [asked, stored, fallback].find(has) ?? null;
}

/** What a plugin shell had registered under its plugin's id when it was taken down, put back when it is installed again. */
interface ShellRegistrations {
  commands: RegisteredCommand[];
  shortcuts: RegisteredShortcut[];
  overlays: (ViewportOverlaySpec & {owner: string})[];
}
const parked = new Map<string, ShellRegistrations>();

function registrationsOf(owner: string): ShellRegistrations {
  return {
    commands: commandsOwnedBy(owner),
    // A command's own key is the command's: it comes back with it.
    shortcuts: shortcutsOwnedBy(owner).filter((s) => !s.id.startsWith("cmd:")),
    overlays: viewportOverlaysOwnedBy(owner),
  };
}

/**
 * Install shell `spec` (null: core's own UI) before the frame renders, which
 * reads it: its modes, its arrangements, the top bar, its panel overrides and
 * core's commands (the palette itself is core's, `CorePaletteHost`). Returns the
 * undo, which an in-place switch runs before installing the next shell:
 * everything a plugin's shell registered under its plugin's id while it was up
 * (commands, shortcuts, HUDs over the view), parked to come back if the shell
 * is installed again. What every install sets (modes, arrangements, the top bar,
 * overrides) the next install sets again.
 */
export function installShell(spec: UiShellSpec | null): () => void {
  if (!spec) {
    setShellModes([]);
    setClassicShell(null);
    setClassicToolbar(true);
    setPanelOverrides(null);
    // Core's commands, for the palette (Ctrl+K) in core's own UI too.
    registerCoreCommands();
    return () => {};
  }
  const modes = shellModes(spec);
  const asked = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get(SHELL_MODE_PARAM) : null;
  setShellModes(modes, initialShellMode(modes, asked, readMode(spec.id), spec.layout?.defaultMode));
  const byMode: Record<string, ClassicArrangement> = {};
  for (const m of spec.layout?.modes ?? []) byMode[m.id] = {grid: m.grid, open: m.open, docks: m.docks, cover: m.viewportCover};
  setClassicShell(
    {
      id: spec.id,
      grid: spec.layout?.grid,
      open: spec.layout?.open,
      docks: spec.layout?.docks,
      modes: byMode,
      groups: spec.chrome?.groups,
      overlays: spec.chrome?.overlays,
      toastAnchor: spec.chrome?.toastAnchor,
      version: spec.layout?.version,
      migrate: spec.layout?.migrate,
    },
    listShellModes().map((m) => m.id),
  );
  setClassicToolbar(spec.chrome?.toolbar !== false);
  setPanelOverrides(spec.panels ?? null);
  // Core's own commands (`CORE_COMMAND_IDS`), for the shell's menus and palette.
  registerCoreCommands();
  // A plugin's shell: what it registered when it was last up comes back (a
  // module-level registration in its chrome runs only once a page).
  const owner = (spec as Partial<RegisteredUiShell>).pluginId;
  const scoped = !!owner && owner !== CORE_PANEL_OWNER;
  const back = scoped ? parked.get(spec.id) : undefined;
  if (back) {
    parked.delete(spec.id);
    for (const {owner: o, ...c} of back.commands) registerCommand(c, o);
    for (const {owner: o, ...s} of back.shortcuts) registerShortcut(s, o);
    for (const {owner: o, ...v} of back.overlays) registerViewportOverlay(v, o);
  }
  const before = scoped ? registrationsOf(owner!) : null;
  return () => {
    if (!before) return;
    // What the plugin registered while its shell was up is the shell's (its
    // chrome's); what it had before (its feature plugin's) stays.
    const had = (ids: {id: string}[]) => new Set(ids.map((x) => x.id));
    const now = registrationsOf(owner!);
    const prev = {c: had(before.commands), s: had(before.shortcuts), o: had(before.overlays)};
    const taken: ShellRegistrations = {
      commands: now.commands.filter((c) => !prev.c.has(c.id)),
      shortcuts: now.shortcuts.filter((s) => !prev.s.has(s.id)),
      overlays: now.overlays.filter((v) => !prev.o.has(v.id)),
    };
    for (const c of taken.commands) unregisterCommand(c.id);
    for (const s of taken.shortcuts) unregisterShortcut(s.id);
    for (const v of taken.overlays) unregisterViewportOverlay(v.id);
    if (taken.commands.length || taken.shortcuts.length || taken.overlays.length) parked.set(spec.id, taken);
  };
}

/**
 * Keep shell `spec` installed: before the first render, and again, after the
 * previous one's undo, whenever an in-place switch hands in another one. The
 * frame below keeps its state, the 3D canvas included.
 */
function useInstalledShell(spec: UiShellSpec | null): void {
  const installed = useRef<{spec: UiShellSpec | null; undo: () => void} | null>(null);
  if (!installed.current || installed.current.spec !== spec) {
    installed.current?.undo();
    installed.current = {spec, undo: installShell(spec)};
  }
  useEffect(
    () => () => {
      installed.current?.undo();
      installed.current = null;
    },
    [],
  );
}

/** Remember the active mode, and let core suspend or restore the FEA colouring for it. */
function useModeEffects(shellId: string | null): void {
  useEffect(() => {
    if (shellId === null) return;
    const report = () => {
      // A shell without modes has nothing to report, as core's own UI does not.
      if (!listShellModes().length) return;
      const id = activeShellMode();
      const mode = listShellModes().find((m) => m.id === id) ?? null;
      notifyActiveModeSceneColor(mode ? {id: mode.id, ownsSceneColor: mode.ownsSceneColor} : null);
      // A mode that shows no deformation holds the FEA model undeformed while it is active.
      setFeaDeformationShown(MODE_DEFORMATION_OWNER, mode?.showsDeformation !== false);
      try {
        if (id) localStorage.setItem(modeKey(shellId), id);
      } catch {
        // Storage unavailable: the shell starts in its first mode next time.
      }
    };
    report();
    const unsubscribe = subscribeShellModes(report);
    return () => {
      unsubscribe();
      setFeaDeformationShown(MODE_DEFORMATION_OWNER, true);
      // Leaving the shell (an in-place switch): a mode that owned the scene
      // colouring gives it back, as leaving that mode would.
      notifyActiveModeSceneColor(null);
    };
  }, [shellId]);
}

/**
 * Core's layout dressed as shell `spec`, or as core's own UI for null. An
 * in-place switch hands in another `spec` (`UiShellHost`, `CoreShellRoot`):
 * the elements around the layout keep their places, so the layout (and the 3D
 * canvas in it) is never remounted; only the chrome, the modes and the look change.
 * `children` replaces the layout (tests).
 */
export default function ComposedLayout({spec, children}: {spec: UiShellSpec | null; children?: React.ReactNode}) {
  useInstalledShell(spec);
  useShellLook(spec ?? undefined);
  useModeEffects(spec?.id ?? null);
  const lazyOf = (load?: () => Promise<{default: React.ComponentType}>) => (load ? React.lazy(load) : null);
  const Header = React.useMemo(() => lazyOf(spec?.chrome?.header), [spec]);
  const Footer = React.useMemo(() => lazyOf(spec?.chrome?.footer), [spec]);
  const Rail = React.useMemo(() => lazyOf(spec?.chrome?.rail), [spec]);
  const slot = (C: React.ComponentType | null) =>
    C && (
      <Suspense fallback={null}>
        <C />
      </Suspense>
    );
  // Every child keeps its position whatever the shell draws (a missing slot is
  // a hole, not a shift), so React keeps the layout's element across a switch.
  return (
    <div className="flex h-full w-full flex-col">
      {slot(Header)}
      <div className="flex min-h-0 w-full flex-1 flex-row">
        {Rail && <div className="relative h-full shrink-0">{slot(Rail)}</div>}
        <div className="relative h-full min-w-0 flex-1">{children ?? <LayoutRoot />}</div>
      </div>
      {slot(Footer)}

      {/* The shortcuts dialog, for a menu's "Keyboard shortcuts" (`core:shortcuts`). */}
      {spec && <ShortcutsDialogHost />}
      {/* Help -> About and File -> Export (`core:about`, `core:export-*`). */}
      {spec && <CoreDialogsHost />}
    </div>
  );
}
