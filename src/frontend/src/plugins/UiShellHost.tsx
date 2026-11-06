// Mounts the active UI shell. This is what `index.tsx` renders instead of
// `<App/>` — core no longer names its own UI at the root any more than it names
// a plugin's.
//
// Two guarantees this host owns, both of which exist so that shipping an image
// whose default UI is an overlaid third-party shell is a safe operation:
//
//   1. **A load failure falls back to core.** A chunk that 404s (stale CDN,
//      half-published build) renders the built-in UI with a console error, not a
//      blank page.
//   2. **A render crash falls back to core.** An error boundary around the shell
//      swaps in the built-in UI once, rather than showing the fullscreen reload
//      card, so a broken alternative UI degrades to a working viewer.
//
// Both are one-shot: if CORE itself fails, the boundary rethrows to the
// fullscreen boundary in `index.tsx`.
//
// It also takes UI-shell switches IN PLACE where it can. Core's own UI and every
// composed shell render under ONE root of core's (`CoreShellRoot` in `@/app`: the
// viewer provider, the canvas, the layout), told which shell to show by a prop;
// the element and its key stay the same across a switch between them, so React
// keeps the whole tree, the 3D canvas and the scene included, and only the
// shell's layout, chrome and look change. A shell with a root of its own (`load`)
// or one that sets `reloadOnSwitch` is switched by a reload (`setActiveUiShell`).

import React, { Suspense, useSyncExternalStore } from "react";

import {
  CORE_UI_SHELL_ID,
  activeUiShell,
  activeUiShellVersion,
  canSwitchInPlace,
  getUiShell,
  setUiShellSwitchHost,
  subscribeActiveUiShell,
  switchesInPlace,
  type UiShellSpec,
} from "./uiShells";

/** Core's root for the shells that run under it, told which one to show. */
export type CoreShellRootComponent = React.ComponentType<{ shellId: string }>;

/** Where core's root comes from: the core shell's module, which the panel bodies load through too. */
export type CoreShellRootLoader = () => Promise<{ default: CoreShellRootComponent }>;

const loadCoreRoot: CoreShellRootLoader = () => import("@/app").then((m) => ({ default: m.CoreShellRoot }));

/** The key the host mounts shell `id` under: one for every shell under core's root, so a switch between them keeps it. */
export const CORE_ROOT_KEY = "core-root";
export function shellRootKey(id: string): string {
  const spec = getUiShell(id);
  return spec && switchesInPlace(spec) ? CORE_ROOT_KEY : `shell:${id}`;
}

/** A shell with a root of its own. */
function loadOwnRoot(shell: UiShellSpec): Promise<{ default: React.ComponentType }> {
  return shell.load!();
}

function lazyOwnShell(id: string, coreRoot: React.ComponentType<{ shellId: string }>): React.LazyExoticComponent<React.ComponentType> {
  return React.lazy(async () => {
    const shell = getUiShell(id);
    if (!shell) throw new Error(`UI shell "${id}" is not registered`);
    try {
      return await loadOwnRoot(shell);
    } catch (err) {
      console.error(`[plugins] UI shell "${id}" failed to load; falling back to core`, err);
      const Fallback = () => React.createElement(coreRoot, { shellId: CORE_UI_SHELL_ID });
      return { default: Fallback };
    }
  });
}

/** Core's root, loaded once; a composed shell's chunks load inside it, a failing one falls back to core there. */
function lazyCoreRoot(load: CoreShellRootLoader): React.LazyExoticComponent<CoreShellRootComponent> {
  return React.lazy(load);
}

interface FallbackState {
  crashed: boolean;
}

/** Swaps a crashing shell for the built-in one. Deliberately NOT the shared
 * ErrorBoundary: that one renders a reload card, and reloading a shell that
 * crashes on mount just crashes again. */
class ShellErrorBoundary extends React.Component<
  { shellId: string; children: React.ReactNode; onFallback: () => void },
  FallbackState
> {
  state: FallbackState = { crashed: false };

  static getDerivedStateFromError(): FallbackState {
    return { crashed: true };
  }

  componentDidCatch(error: Error): void {
    if (this.props.shellId === CORE_UI_SHELL_ID) throw error;
    console.error(
      `[plugins] UI shell "${this.props.shellId}" crashed; falling back to core UI. ` +
        `Add ?ui=${CORE_UI_SHELL_ID} to force the built-in UI.`,
      error,
    );
    this.props.onFallback();
  }

  render(): React.ReactNode {
    return this.state.crashed ? null : this.props.children;
  }
}

const UiShellHost: React.FC<{ coreRoot?: CoreShellRootLoader }> = ({ coreRoot = loadCoreRoot }) => {
  // Re-rendered by an in-place switch; the id is the active shell's.
  useSyncExternalStore(subscribeActiveUiShell, activeUiShellVersion, activeUiShellVersion);
  const active = activeUiShell().id;
  // After a crash, core's own UI for the rest of the page.
  const [crashed, setCrashed] = React.useState(false);
  const shellId = crashed ? CORE_UI_SHELL_ID : active;
  const key = crashed ? "fallback" : shellRootKey(shellId);
  const CoreRoot = React.useMemo(() => lazyCoreRoot(coreRoot), [coreRoot]);
  const OwnShell = React.useMemo(() => (key === CORE_ROOT_KEY || crashed ? null : lazyOwnShell(shellId, CoreRoot)), [key, shellId, crashed, CoreRoot]);

  // While core's root is mounted it takes switches between the shells under it.
  // (After a crash, none: every switch reloads.)
  React.useEffect(() => {
    if (key !== CORE_ROOT_KEY) return;
    return setUiShellSwitchHost((to) => canSwitchInPlace(shellId, to));
  }, [key, shellId]);

  return (
    <ShellErrorBoundary key={key} shellId={shellId} onFallback={() => setCrashed(true)}>
      <Suspense fallback={null}>{OwnShell ? <OwnShell /> : <CoreRoot shellId={shellId} />}</Suspense>
    </ShellErrorBoundary>
  );
};

export default UiShellHost;
