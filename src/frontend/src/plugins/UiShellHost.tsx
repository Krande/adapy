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
  onUiShellSwitchNotice,
  setActiveUiShell,
  setUiShellSwitchHost,
  subscribeActiveUiShell,
  switchesInPlace,
  type UiShellSpec,
  type UiShellSwitchNotice,
} from "./uiShells";
import {skippedPluginReason} from "./registry";

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
  error: Error | null;
}

/** What a shell crash leaves behind, read back by `UiShellHost` on the next mount in the session
 * (a reload included) so the one-time recovered-from notice -- and anything else that cares why a
 * crash just happened -- survives the remount. `sessionStorage` only (never a wire call): this is
 * a LOCAL diagnostic, not a server-side report. Exported for tests, which write/read it directly
 * to set up "a previous mount crashed" without having to crash a real shell first. */
export const UI_FALLBACK_KEY = "ada:ui-fallback";

interface StoredFallback {
  shellId: string;
  message: string;
  at: number;
}

function readStoredFallback(): StoredFallback | null {
  try {
    const raw = sessionStorage.getItem(UI_FALLBACK_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredFallback>;
    return typeof parsed.shellId === "string" && typeof parsed.message === "string" && typeof parsed.at === "number"
      ? (parsed as StoredFallback)
      : null;
  } catch {
    return null;
  }
}

function writeStoredFallback(info: StoredFallback): void {
  try {
    sessionStorage.setItem(UI_FALLBACK_KEY, JSON.stringify(info));
  } catch {
    // Unavailable (private mode, blocked site data): the banner still shows from React state for
    // this mount; it just does not survive a reload.
  }
}

function clearStoredFallback(): void {
  try {
    sessionStorage.removeItem(UI_FALLBACK_KEY);
  } catch {
    /* nothing stored, or storage unavailable -- either way, nothing to clear */
  }
}

const bannerButtonStyle: React.CSSProperties = {
  background: "transparent",
  color: "inherit",
  border: "1px solid rgba(255,255,255,0.6)",
  borderRadius: 4,
  padding: "2px 10px",
  cursor: "pointer",
  fontSize: 12,
  flexShrink: 0,
};

/** No silent fallback. A persistent banner names the crash and gives a way back (a real
 * reload -- the crash may be a stale chunk, which only a refetch clears) and a way to report it
 * (the message, the shell id and when, as text). Rendered OVER the fallback UI, not instead of it:
 * Classic stays fully usable underneath -- but it sits at the very top of the viewport, exactly
 * where Classic's own header (its menus, its interface switcher) lives, so it is dismissible: a
 * reader who has seen it can get the header back without reloading past the crash. Dismissing
 * does not touch `crashed` itself -- core keeps standing in for the shell that crashed -- it only
 * stops saying so at the top of the screen. The caller mounts one keyed by `info.at`, so a FRESH
 * crash (a new `at`) gets a fresh instance and is never hidden by a dismissal of an earlier one.
 *
 * Named by the shell that actually crashed (`getUiShell(info.shellId)?.label`), never the literal
 * "Docked UI": a shell other than the one named `docked` crashing must not be misreported as if it
 * were. `info.shellId` is never core's own id -- `ShellErrorBoundary` rethrows a core crash instead
 * of calling `onFallback` -- but the fallback still reads from it rather than hard-coding a name, so
 * that stays true by construction instead of by convention. */
const FallbackBanner: React.FC<{ info: StoredFallback }> = ({ info }) => {
  const [copied, setCopied] = React.useState(false);
  const [dismissed, setDismissed] = React.useState(false);
  const label = getUiShell(info.shellId)?.label ?? info.shellId;
  const details = `${label} crashed: ${info.message}\nShell: ${info.shellId}\nAt: ${new Date(info.at).toISOString()}`;
  if (dismissed) return null;
  return (
    <div
      role="alert"
      style={{
        position: "fixed",
        insetInline: 0,
        top: 0,
        zIndex: 100000,
        background: "#7a1f1f",
        color: "#fff",
        padding: "6px 12px",
        display: "flex",
        alignItems: "center",
        gap: 12,
        fontSize: 13,
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
        {label} crashed: {info.message}. Showing Classic.
      </span>
      <button
        type="button"
        style={bannerButtonStyle}
        onClick={() => {
          // Cleared BEFORE the reload: if this attempt succeeds, the fresh mount must not show a
          // stale banner for a crash this very click is trying to get past.
          clearStoredFallback();
          window.location.reload();
        }}
      >
        Reload {label}
      </button>
      <button
        type="button"
        style={bannerButtonStyle}
        onClick={() => {
          void navigator.clipboard?.writeText(details).then(
            () => setCopied(true),
            () => {},
          );
        }}
      >
        {copied ? "Copied" : "Copy details"}
      </button>
      <button type="button" aria-label="Dismiss" style={bannerButtonStyle} onClick={() => setDismissed(true)}>
        ✕
      </button>
    </div>
  );
};

/** How `UiShellSwitchReason` reads in the toast ("Switched to <name> (<reason>)"). */
const SWITCH_REASON_WORDS: Record<UiShellSwitchNotice["reason"], string> = {
  switcher: "interface switcher",
  palette: "command palette",
  options: "Options panel",
};

/** Every deliberate switch carries a reason and shows a toast, instead of happening
 * silently with at most a console message -- a toast per switch (not a running log), auto-
 * dismissed, with the way back. Lives in `UiShellHost` (always mounted) rather than core's own
 * toast stack: a shell switch is not a job or a load, and the stack's rows assume one. */
const SwitchToast: React.FC<{ notice: UiShellSwitchNotice }> = ({ notice }) => {
  const [dismissed, setDismissed] = React.useState(false);
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDismissed(true), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);
  if (dismissed) return null;
  return (
    <div
      role="status"
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        zIndex: 99999,
        background: "#24292e",
        color: "#fff",
        padding: "8px 12px",
        borderRadius: 6,
        display: "flex",
        alignItems: "center",
        gap: 12,
        fontSize: 13,
        fontFamily: "system-ui, sans-serif",
        boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
      }}
    >
      <span>
        Switched to {notice.toLabel} ({SWITCH_REASON_WORDS[notice.reason]})
      </span>
      <button
        type="button"
        style={bannerButtonStyle}
        onClick={() => {
          setDismissed(true);
          setActiveUiShell(notice.fromId, { reason: notice.reason });
        }}
      >
        Switch back
      </button>
    </div>
  );
};

/** A boot toast for when the shell `?ui=`/stored/default asked for was refused
 * (`activeUiShell().rejected`) -- so "it quietly booted into Classic" is a toast, not only the
 * existing `console.warn`. Enriched with WHY when the rejection traces to a plugin
 * `registerPlugin` skipped (`skippedPluginReason`): the shell's `pluginId` is the id a UI-shell
 * plugin's spec registers under, so a version-range skip there is the common cause. Shown once
 * per page boot (module state, not sessionStorage -- a toast about THIS boot, not a memory that
 * outlives it). */
let _bootRejectionToastShownThisPage = false;

const BootRejectionToast: React.FC<{ rejectedId: string; activeLabel: string }> = ({ rejectedId, activeLabel }) => {
  const [dismissed, setDismissed] = React.useState(false);
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDismissed(true), 8000);
    return () => window.clearTimeout(timer);
  }, []);
  if (dismissed) return null;
  const why = skippedPluginReason(rejectedId);
  return (
    <div
      role="status"
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        zIndex: 99999,
        background: "#24292e",
        color: "#fff",
        padding: "8px 12px",
        borderRadius: 6,
        fontSize: 13,
        fontFamily: "system-ui, sans-serif",
        boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
        maxWidth: 360,
      }}
    >
      UI "{rejectedId}" is not available{why ? ` (${why})` : ""}; showing {activeLabel}.
    </div>
  );
};

/** The one-time notice for a record a PREVIOUS mount left behind (sessionStorage, read once at
 * mount) when THIS mount's own shell did not crash again: a plain reload (F5, the reload card, the
 * automatic reload after a failed chunk load) that got past whatever crashed before. Unlike
 * `FallbackBanner`, this never claims "Showing Classic" -- that would be true of the crash this
 * describes, not of what is on screen now, since `crashed` is not derived from this record (see
 * `UiShellHost`) -- and it auto-dismisses like the other toasts, rather than sitting over the
 * header like a live fallback does. */
const RecoveredFallbackToast: React.FC<{ info: StoredFallback }> = ({ info }) => {
  const [dismissed, setDismissed] = React.useState(false);
  React.useEffect(() => {
    const timer = window.setTimeout(() => setDismissed(true), 8000);
    return () => window.clearTimeout(timer);
  }, []);
  if (dismissed) return null;
  const label = getUiShell(info.shellId)?.label ?? info.shellId;
  return (
    <div
      role="status"
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        zIndex: 99999,
        background: "#24292e",
        color: "#fff",
        padding: "8px 12px",
        borderRadius: 6,
        fontSize: 13,
        fontFamily: "system-ui, sans-serif",
        boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
        maxWidth: 360,
      }}
    >
      {label} crashed before this reload: {info.message}
    </div>
  );
};

/** Swaps a crashing shell for the built-in one. Deliberately NOT the shared
 * ErrorBoundary: that one renders a reload card, and reloading a shell that
 * crashes on mount just crashes again. */
class ShellErrorBoundary extends React.Component<
  { shellId: string; children: React.ReactNode; onFallback: (error: Error) => void },
  FallbackState
> {
  state: FallbackState = { crashed: false, error: null };

  static getDerivedStateFromError(error: Error): FallbackState {
    return { crashed: true, error };
  }

  componentDidCatch(error: Error): void {
    if (this.props.shellId === CORE_UI_SHELL_ID) throw error;
    console.error(
      `[plugins] UI shell "${this.props.shellId}" crashed; falling back to core UI. ` +
        `Add ?ui=${CORE_UI_SHELL_ID} to force the built-in UI.`,
      error,
    );
    this.props.onFallback(error);
  }

  render(): React.ReactNode {
    return this.state.crashed ? null : this.props.children;
  }
}

const UiShellHost: React.FC<{ coreRoot?: CoreShellRootLoader }> = ({ coreRoot = loadCoreRoot }) => {
  // Re-rendered by an in-place switch; the id is the active shell's.
  useSyncExternalStore(subscribeActiveUiShell, activeUiShellVersion, activeUiShellVersion);
  const active = activeUiShell().id;
  // Core's own UI for the rest of the page once a shell crashes -- but ONLY from a render error
  // THIS mount actually caught (`onFallback`, below), set through real React state. Never seeded
  // from the stored record (see `recoveredFrom`): a stale record read back on every later mount
  // used to force core, with nothing that ever cleared it short of its own "Reload" button -- so a
  // crash pinned Classic for the rest of the browser tab across every ordinary reload, and even
  // across an explicit switch back to the shell that crashed (the switch is core -> X, so no fresh
  // crash to catch and nothing else watched for it).
  const [crashInfo, setCrashInfo] = React.useState<StoredFallback | null>(null);
  const crashed = crashInfo !== null;
  const shellId = crashed ? CORE_UI_SHELL_ID : active;
  const key = crashed ? "fallback" : shellRootKey(shellId);
  const CoreRoot = React.useMemo(() => lazyCoreRoot(coreRoot), [coreRoot]);
  const OwnShell = React.useMemo(() => (key === CORE_ROOT_KEY || crashed ? null : lazyOwnShell(shellId, CoreRoot)), [key, shellId, crashed, CoreRoot]);

  // The record a PREVIOUS mount left in sessionStorage (a crash, then an ordinary reload -- F5,
  // the reload card, or the automatic reload after a failed chunk load -- rather than this host's
  // own "Reload" button, which clears it first). Read once, and shown as a one-time notice
  // (`RecoveredFallbackToast`, below) rather than fed into `crashed` above: it describes a PAST
  // mount, not this one, and this one may well be showing the real shell just fine.
  const [recoveredFrom] = React.useState<StoredFallback | null>(readStoredFallback);
  React.useEffect(() => {
    // Captured into this mount's own state just above; the persisted copy has done its one job of
    // surviving the remount and must not go on describing whatever crashes (or doesn't) next.
    clearStoredFallback();
  }, []);

  // While core's root is mounted it takes switches between the shells under it.
  // (After a crash, none: every switch reloads.)
  React.useEffect(() => {
    if (key !== CORE_ROOT_KEY) return;
    return setUiShellSwitchHost((to) => canSwitchInPlace(shellId, to));
  }, [key, shellId]);

  // The latest switch toast, one at a time (a second switch replaces the first's, rather
  // than stacking -- the user just acted again, the old notice is moot).
  const [switchNotice, setSwitchNotice] = React.useState<UiShellSwitchNotice | null>(null);
  React.useEffect(() => onUiShellSwitchNotice(setSwitchNotice), []);

  // The boot rejection toast, once per page.
  const rejected = activeUiShell().rejected;
  const [showBootRejection, setShowBootRejection] = React.useState(false);
  React.useEffect(() => {
    if (!rejected || _bootRejectionToastShownThisPage) return;
    _bootRejectionToastShownThisPage = true;
    setShowBootRejection(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `rejected` is read once at boot,
    // deliberately: a later in-place switch must not re-evaluate this effect against it.
  }, []);

  // An active-shell change means core is no longer standing in for a crash: Classic picked on
  // purpose (the switcher, the palette, Options) is Classic, not a frozen fallback, and picking
  // the crashed shell again deserves a fresh try rather than yesterday's banner.
  React.useEffect(
    () =>
      subscribeActiveUiShell(() => {
        setCrashInfo(null);
        clearStoredFallback();
      }),
    [],
  );

  const onFallback = React.useCallback(
    (error: Error) => {
      const info: StoredFallback = { shellId: active, message: error.message || String(error), at: Date.now() };
      writeStoredFallback(info);
      setCrashInfo(info);
    },
    [active],
  );

  return (
    <>
      {crashInfo && crashInfo.shellId !== CORE_UI_SHELL_ID && <FallbackBanner key={crashInfo.at} info={crashInfo} />}
      {!crashInfo && recoveredFrom && recoveredFrom.shellId !== CORE_UI_SHELL_ID && (
        <RecoveredFallbackToast key={recoveredFrom.at} info={recoveredFrom} />
      )}
      {switchNotice && <SwitchToast notice={switchNotice} />}
      {showBootRejection && rejected && (
        <BootRejectionToast rejectedId={rejected.id} activeLabel={getUiShell(active)?.label ?? active} />
      )}
      <ShellErrorBoundary key={key} shellId={shellId} onFallback={onFallback}>
        <Suspense fallback={null}>{OwnShell ? <OwnShell /> : <CoreRoot shellId={shellId} />}</Suspense>
      </ShellErrorBoundary>
    </>
  );
};

export default UiShellHost;
