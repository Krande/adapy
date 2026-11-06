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

import React, { Suspense } from "react";

import { CORE_UI_SHELL_ID, activeUiShell, getUiShell, type UiShellSpec } from "./uiShells";

/** A shell's root: its own, or core's root composed from the shell's layout. */
function loadShell(shell: UiShellSpec): Promise<{ default: React.ComponentType }> {
  if (shell.load) return shell.load();
  // Through the core shell's module, which the panel bodies load through too.
  return import("@/app").then((m) => ({ default: m.composedShell(shell) }));
}

function lazyShell(id: string): React.LazyExoticComponent<React.ComponentType> {
  return React.lazy(async () => {
    const shell = getUiShell(id);
    if (!shell) throw new Error(`UI shell "${id}" is not registered`);
    try {
      return await loadShell(shell);
    } catch (err) {
      if (id === CORE_UI_SHELL_ID) throw err;
      console.error(`[plugins] UI shell "${id}" failed to load; falling back to core`, err);
      return await loadShell(getUiShell(CORE_UI_SHELL_ID)!);
    }
  });
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

const UiShellHost: React.FC = () => {
  const initial = React.useMemo(() => activeUiShell().id, []);
  const [shellId, setShellId] = React.useState(initial);
  const Shell = React.useMemo(() => lazyShell(shellId), [shellId]);

  return (
    <ShellErrorBoundary
      key={shellId}
      shellId={shellId}
      onFallback={() => setShellId(CORE_UI_SHELL_ID)}
    >
      <Suspense fallback={null}>
        <Shell />
      </Suspense>
    </ShellErrorBoundary>
  );
};

export default UiShellHost;
