// A UI shell composed from core (`UiShellSpec` without `load`): core's layout of
// floating and dockable panels, in documents of the shell's own (one per mode),
// starting from the shell's grids, under the shell's header and theme tokens.
// `AppRoot` (in `@/app`) wraps it in the routes, the sign-in gate and the viewer
// provider that every shell shares.

import React, {Suspense, useEffect, useState} from "react";

import {getPluginModes} from "@/plugins/registry";
import type {UiShellSpec} from "@/plugins/uiShells";
import {notifyActiveModeSceneColor} from "@/utils/scene/fea/modeSceneColor";

import LayoutRoot from "./LayoutRoot";
import {setClassicShell, setClassicToolbar, type ClassicArrangement} from "./presets/classicPlace";
import {activeShellMode, listShellModes, setShellModes, subscribeShellModes, type ShellMode} from "./shellModes";

/** Set `tokens` on the document root while mounted. Portalled overlays read them too. */
function useThemeTokens(tokens: Record<string, string> | undefined): void {
  useEffect(() => {
    if (!tokens) return;
    const root = document.documentElement.style;
    const before = Object.keys(tokens).map((k) => [k, root.getPropertyValue(k)] as const);
    for (const [k, v] of Object.entries(tokens)) root.setProperty(k, v);
    return () => {
      for (const [k, v] of before) {
        if (v) root.setProperty(k, v);
        else root.removeProperty(k);
      }
    };
  }, [tokens]);
}

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
function shellModes(spec: UiShellSpec): ShellMode[] {
  const own = spec.layout?.modes ?? [];
  if (!own.length) return [];
  const mine: ShellMode[] = own.map((m, i) => ({id: m.id, label: m.label, hint: m.hint, icon: m.icon, order: m.order ?? i}));
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
      toolbar: mode.toolbar,
    }));
  return [...mine, ...theirs];
}

/** Install the shell before the frame's first render, which reads it. */
function installShell(spec: UiShellSpec): void {
  const modes = shellModes(spec);
  setShellModes(modes, readMode(spec.id));
  const byMode: Record<string, ClassicArrangement> = {};
  for (const m of spec.layout?.modes ?? []) byMode[m.id] = {grid: m.grid, open: m.open, docks: m.docks};
  setClassicShell(
    {id: spec.id, grid: spec.layout?.grid, open: spec.layout?.open, docks: spec.layout?.docks, modes: byMode},
    listShellModes().map((m) => m.id),
  );
  setClassicToolbar(spec.chrome?.toolbar !== false);
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
      try {
        if (id) localStorage.setItem(modeKey(shellId), id);
      } catch {
        // Storage unavailable: the shell starts in its first mode next time.
      }
    };
    report();
    return subscribeShellModes(report);
  }, [shellId]);
}

export default function ComposedLayout({spec}: {spec: UiShellSpec}) {
  useState(() => {
    installShell(spec);
    return null;
  });
  useThemeTokens(spec.themeTokens);
  useModeEffects(spec.id);
  const Header = React.useMemo(() => (spec.chrome?.header ? React.lazy(spec.chrome.header) : null), [spec]);
  return (
    <div className="flex h-full w-full flex-col">
      {Header && (
        <Suspense fallback={null}>
          <Header />
        </Suspense>
      )}
      <div className="relative min-h-0 w-full flex-1">
        <LayoutRoot />
      </div>
    </div>
  );
}
