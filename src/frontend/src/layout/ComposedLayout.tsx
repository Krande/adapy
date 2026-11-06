// A UI shell composed from core (`UiShellSpec` without `load`): core's layout of
// floating and dockable panels, in documents of the shell's own (one per mode),
// starting from the shell's grids, under the shell's header and theme tokens.
// `AppRoot` (in `@/app`) wraps it in the routes, the sign-in gate and the viewer
// provider that every shell shares.

import React, {Suspense, useEffect, useState} from "react";

import {getPluginModes} from "@/plugins/registry";
import type {RegisteredUiShell, UiShellSpec} from "@/plugins/uiShells";
import {setFeaDeformationShown} from "@/utils/scene/fea/deformationGate";
import {notifyActiveModeSceneColor} from "@/utils/scene/fea/modeSceneColor";

import {useShellLook} from "./shellTheme";
import {CommandPaletteHost, openCommandPalette} from "./CommandPalette";
import {registerCommand} from "./commands";
import {registerCoreCommands} from "./coreCommands";
import {ShortcutsDialogHost} from "@/components/options/ShortcutsModal";
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

/** Install the shell before the frame's first render, which reads it. */
function installShell(spec: UiShellSpec): void {
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
  const palette = spec.chrome?.commandPalette;
  if (palette) {
    registerCommand(
      {id: "core:command-palette", label: "Command palette", group: "View", keys: palette.keys, run: openCommandPalette},
      "core",
    );
  }
}

/** Remember the active mode, and let core suspend or restore the FEA colouring for it. */
function useModeEffects(shellId: string): void {
  useEffect(() => {
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
    };
  }, [shellId]);
}

export default function ComposedLayout({spec}: {spec: UiShellSpec}) {
  useState(() => {
    installShell(spec);
    return null;
  });
  useShellLook(spec);
  useModeEffects(spec.id);
  const lazyOf = (load?: () => Promise<{default: React.ComponentType}>) => (load ? React.lazy(load) : null);
  const Header = React.useMemo(() => lazyOf(spec.chrome?.header), [spec]);
  const Footer = React.useMemo(() => lazyOf(spec.chrome?.footer), [spec]);
  const Rail = React.useMemo(() => lazyOf(spec.chrome?.rail), [spec]);
  const slot = (C: React.ComponentType | null) =>
    C && (
      <Suspense fallback={null}>
        <C />
      </Suspense>
    );
  return (
    <div className="flex h-full w-full flex-col">
      {slot(Header)}
      <div className="flex min-h-0 w-full flex-1 flex-row">
        {Rail && <div className="relative h-full shrink-0">{slot(Rail)}</div>}
        <div className="relative h-full min-w-0 flex-1">
          <LayoutRoot />
        </div>
      </div>
      {slot(Footer)}
      {spec.chrome?.commandPalette && <CommandPaletteHost />}
      {/* The shortcuts dialog, for a menu's "Keyboard shortcuts" (`core:shortcuts`). */}
      <ShortcutsDialogHost />
    </div>
  );
}
