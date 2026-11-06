// The window bus of THIS page (`./windowBus`), and what the main window and the
// pop-out windows do with it:
//
//   * `windowBus()` -- this page's bus, created and started on first use, over a
//     `BroadcastChannel` scoped by path and UI shell; it says goodbye on
//     `pagehide`. `useOpenWindows()` lists the other live windows.
//   * `installMainWindow` -- the main window's part: it docks the panels other
//     windows send it, hides here what another window shows (and reveals it
//     there instead), takes back the panels of a window that closes, keeps the
//     dashboard record and offers "Reopen windows".
//   * `moveToWindowItem` -- the "Move to window" submenu of a panel's Move menu.
//
// Touches `window` / `localStorage` only inside functions, so importing it is
// safe under `node --test`.

import {useSyncExternalStore} from "react";

import {bootUiShellId} from "@/plugins/uiShells";

import {registerCommand, unregisterCommand} from "./commands";
import type {PanelId, PanelMenuItem, RegisteredPanel} from "./panel";
import {setPlacedInOtherWindows} from "./placedElsewhere";
import {canPopOut, openWindow, popoutPanelId, popoutSession, popoutWindowId} from "./popout";
import {listPanels} from "./registry";
import {setRevealElsewhere} from "./reveal";
import {subscribePanels} from "./panelStates";
import {
  MAIN_WINDOW_ID,
  MAIN_WINDOW_LABEL,
  acceptPanels,
  broadcastTransport,
  claimSession,
  closedDashboardWindows,
  createWindowBus,
  defaultWindowLabel,
  forgetWindow,
  isSessionId,
  mainWindowLive,
  newSessionId,
  nextWindowId,
  panelsToReturn,
  readDashboard,
  recordWindow,
  resolveSessionId,
  sendPanel,
  windowBusChannelName,
  type DashboardRecord,
  type DashboardWindow,
  type WindowBus,
  type WindowBusTransport,
  type WindowId,
  type WindowInfo,
} from "./windowBus";

// ---------------------------------------------------------------------------
// Storage keys, per UI shell.
// ---------------------------------------------------------------------------

// The shell the page booted in: its bus and its dashboard stay that shell's
// through an in-place switch, and a pop-out it opens boots into it (`popout.ts`).
const shell = () => bootUiShellId();
/** A pop-out window's arrangement (its own `LayoutPrefs`, apart from the main window's), in dashboard session `session`. */
export const windowLayoutKey = (id: WindowId, session: string = dashboardSession(), shellId: string = shell()) =>
  `ada:layout-window:${shellId}:${session}:${id}`;
/** A pop-out window's label as the user named it, in dashboard session `session`. */
export const windowLabelKey = (id: WindowId, session: string = dashboardSession(), shellId: string = shell()) =>
  `ada:window-label:${shellId}:${session}:${id}`;
/** The main window's record of the dashboard's windows, for dashboard session `session`. */
export const dashboardKey = (session: string = dashboardSession(), shellId: string = shell()) => `ada:dashboard:${shellId}:${session}`;
/** The session whose dashboard was recorded last on this shell: a new session offers to reopen its windows. */
export const lastDashboardKey = (shellId: string = shell()) => `ada:dashboard-last:${shellId}`;
/** The sessions known on this shell, with when each was last used (what pruning goes by). */
export const sessionsKey = (shellId: string = shell()) => `ada:dashboard-sessions:${shellId}`;
/** Where a tab keeps its main window's session id (sessionStorage: it survives a reload, not a browser restart). */
const tabSessionKey = (shellId: string = shell()) => `ada:dashboard-session:${shellId}`;

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  /** Enumeration (`Storage` has it): lets pruning find every key of a session. */
  readonly length?: number;
  key?(index: number): string | null;
}

function browserStorage(): KeyValueStorage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

function read(storage: KeyValueStorage | null, key: string): string | null {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function write(storage: KeyValueStorage | null, key: string, value: string | null): void {
  try {
    if (value === null) storage?.removeItem(key);
    else storage?.setItem(key, value);
  } catch {
    // Kept for this page only.
  }
}

/** The label stored for pop-out `id`, or null. */
export function storedWindowLabel(id: WindowId, storage = browserStorage(), session: string = dashboardSession()): string | null {
  return read(storage, windowLabelKey(id, session));
}

export function storeWindowLabel(id: WindowId, label: string, storage = browserStorage(), session: string = dashboardSession()): void {
  write(storage, windowLabelKey(id, session), label.trim() || null);
}

// ---------------------------------------------------------------------------
// Sessions: remembered, and pruned so storage does not grow for ever.
// ---------------------------------------------------------------------------

/** At most this many sessions are kept per shell (the current and the last dashboard's always). */
export const SESSION_KEEP = 8;
/** A session unused for this long is pruned, in ms. */
export const SESSION_MAX_AGE_MS = 14 * 24 * 3600 * 1000;

interface SessionRegistry {
  v: 1;
  at: Record<string, number>;
}

function readRegistry(storage: KeyValueStorage | null, shellId: string): SessionRegistry {
  const raw = parseJson(read(storage, sessionsKey(shellId))) as Partial<SessionRegistry> | null;
  const at: Record<string, number> = {};
  if (raw && raw.v === 1 && raw.at && typeof raw.at === "object") {
    for (const [id, t] of Object.entries(raw.at)) if (isSessionId(id) && typeof t === "number" && Number.isFinite(t)) at[id] = t;
  }
  return {v: 1, at};
}

/** Note that `session` is in use (now). */
export function touchSession(storage: KeyValueStorage | null, session: string, now: number = Date.now(), shellId: string = shell()): void {
  const reg = readRegistry(storage, shellId);
  if (reg.at[session] === now) return;
  reg.at[session] = now;
  write(storage, sessionsKey(shellId), JSON.stringify(reg));
}

/** The session recorded as the last dashboard's on this shell, or null. */
export function lastDashboardSession(storage: KeyValueStorage | null, shellId: string = shell()): string | null {
  const raw = parseJson(read(storage, lastDashboardKey(shellId))) as {v?: number; session?: unknown} | null;
  return raw && raw.v === 1 && isSessionId(raw.session) ? raw.session : null;
}

function keysOf(storage: KeyValueStorage | null): string[] {
  if (!storage || typeof storage.length !== "number" || typeof storage.key !== "function") return [];
  const out: string[] = [];
  try {
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k) out.push(k);
    }
  } catch {
    // Not enumerable: only what the registry and the records name is pruned.
  }
  return out;
}

/** The storage keys that belong to `session` (windows' layouts and labels, and the dashboard record). */
function sessionKeys(storage: KeyValueStorage | null, session: string, shellId: string): string[] {
  const own = [`ada:layout-window:${shellId}:${session}:`, `ada:window-label:${shellId}:${session}:`];
  const keys = new Set(keysOf(storage).filter((k) => own.some((p) => k.startsWith(p))));
  keys.add(dashboardKey(session, shellId));
  for (const w of readDashboard(parseJson(read(storage, dashboardKey(session, shellId)))).windows) {
    keys.add(windowLayoutKey(w.id, session, shellId));
    keys.add(windowLabelKey(w.id, session, shellId));
  }
  return [...keys];
}

/** Every session with keys in storage on this shell. */
function sessionsInStorage(storage: KeyValueStorage | null, shellId: string): string[] {
  const found = new Set<string>();
  const prefixes = [`ada:layout-window:${shellId}:`, `ada:window-label:${shellId}:`, `ada:dashboard:${shellId}:`];
  for (const k of keysOf(storage)) {
    for (const p of prefixes) {
      if (!k.startsWith(p)) continue;
      const s = k.slice(p.length).split(":")[0];
      if (isSessionId(s)) found.add(s);
    }
  }
  return [...found];
}

export interface PruneOptions {
  /** Sessions never pruned (the current one, the last dashboard's). */
  keep: readonly (string | null | undefined)[];
  now?: number;
  shellId?: string;
  maxCount?: number;
  maxAgeMs?: number;
}

/**
 * Drop the sessions beyond the newest `maxCount` (default `SESSION_KEEP`, the
 * kept ones counted) and those unused for `maxAgeMs`, with their keys. A session
 * that has keys but was never registered counts as oldest. The ids dropped.
 */
export function pruneSessions(storage: KeyValueStorage | null, opts: PruneOptions): string[] {
  const shellId = opts.shellId ?? shell();
  const now = opts.now ?? Date.now();
  const reg = readRegistry(storage, shellId);
  const keep = new Set(opts.keep.filter((s): s is string => !!s));
  const ids = new Set([...Object.keys(reg.at), ...sessionsInStorage(storage, shellId)]);
  const others = [...ids].filter((s) => !keep.has(s)).sort((a, b) => (reg.at[b] ?? 0) - (reg.at[a] ?? 0));
  const room = Math.max(0, (opts.maxCount ?? SESSION_KEEP) - keep.size);
  const maxAge = opts.maxAgeMs ?? SESSION_MAX_AGE_MS;
  const survivors = new Set(others.filter((s) => now - (reg.at[s] ?? 0) <= maxAge).slice(0, room));
  const dropped = others.filter((s) => !survivors.has(s));
  if (!dropped.length) return dropped;
  for (const s of dropped) {
    for (const k of sessionKeys(storage, s, shellId)) write(storage, k, null);
    delete reg.at[s];
  }
  write(storage, sessionsKey(shellId), JSON.stringify(reg));
  return dropped;
}

// ---------------------------------------------------------------------------
// This page's bus.
// ---------------------------------------------------------------------------

/**
 * This page's window id: `?window=`, else for a page opened on `?popout=<panel>`
 * alone one derived from the panel, else the main window's.
 */
export function thisWindowId(): WindowId {
  const own = popoutWindowId();
  if (own) return own;
  const panel = popoutPanelId();
  if (panel) return `p-${panel.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 36)}`;
  return MAIN_WINDOW_ID;
}

let sessionId: string | null = null;

function tabStorage(): KeyValueStorage | null {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

const pagePath = () => (typeof window !== "undefined" ? window.location.pathname : "/");

/**
 * This page's dashboard session. A pop-out has the one in its URL (`?session=`);
 * one opened without (an old link, `?popout=` typed by hand) is STANDALONE: it
 * gets a session of its own that no main window shares, so it works on its own
 * grid and its Move menu offers only "New window". A main window keeps its id in
 * the tab's sessionStorage (a reload keeps the dashboard) and starts without one
 * the first time. `ensureDashboardSession` settles it before the page renders.
 */
export function dashboardSession(): string {
  if (sessionId) return sessionId;
  if (thisWindowId() !== MAIN_WINDOW_ID) {
    sessionId = popoutSession() ?? newSessionId();
    return sessionId;
  }
  const tab = tabStorage();
  const stored = read(tab, tabSessionKey());
  sessionId = isSessionId(stored) ? stored : newSessionId();
  write(tab, tabSessionKey(), sessionId);
  return sessionId;
}

/**
 * Settle this page's session before anything uses it: a main window whose tab
 * kept an id asks the session's channel whether a live main window holds it
 * (a duplicated tab copies sessionStorage), and takes a new id when one does.
 */
export async function ensureDashboardSession(): Promise<string> {
  if (sessionId) return sessionId;
  if (thisWindowId() !== MAIN_WINDOW_ID) return dashboardSession();
  const tab = tabStorage();
  const stored = read(tab, tabSessionKey());
  if (!isSessionId(stored)) return dashboardSession();
  const id = await resolveSessionId(stored, (s) => broadcastTransport(windowBusChannelName(pagePath(), shell(), s)));
  if (!sessionId) {
    sessionId = id;
    write(tab, tabSessionKey(), id);
  }
  return sessionId;
}

/** Set this page's session (tests); null forgets it. */
export function setDashboardSession(id: string | null): void {
  sessionId = id;
}

let pageBus: WindowBus | null = null;
/** Pop-out windows this page opened, by id: focusing one through its handle works where `focus()` from inside does not. */
const opened = new Map<WindowId, Window>();

/** This page's bus, started on first use (a no-op transport where there is no `BroadcastChannel`). */
export function windowBus(): WindowBus {
  if (pageBus) return pageBus;
  const id = thisWindowId();
  const session = dashboardSession();
  touchSession(browserStorage(), session);
  const label = id === MAIN_WINDOW_ID ? MAIN_WINDOW_LABEL : (storedWindowLabel(id) ?? defaultWindowLabel(id));
  const bus = createWindowBus({
    id,
    label,
    transport: broadcastTransport(windowBusChannelName(pagePath(), shell(), session)),
    timers: typeof window !== "undefined",
  });
  pageBus = bus;
  bus.on("focus", (msg) => {
    if (msg.to !== bus.id) return;
    try {
      window.focus();
    } catch {
      // Not allowed: the window stays where it is.
    }
    if (msg.panelId) for (const cb of [...focusPanel]) cb(msg.panelId);
  });
  if (typeof window !== "undefined") {
    window.addEventListener("pagehide", () => bus.stop());
    window.addEventListener("pageshow", (e) => {
      if ((e as PageTransitionEvent).persisted) bus.start();
    });
  }
  bus.start();
  return bus;
}

/** Replace this page's bus (tests); null forgets it. */
export function setWindowBus(bus: WindowBus | null): void {
  pageBus = bus;
}

const focusPanel = new Set<(id: PanelId) => void>();

/** Called when another window asks this one to show `id` (after it was brought forward). */
export function onFocusPanel(cb: (id: PanelId) => void): () => void {
  focusPanel.add(cb);
  return () => void focusPanel.delete(cb);
}

/** Bring window `id` forward, on `panel` when given: through the handle this page holds, and by asking it. */
export function focusWindow(bus: WindowBus, id: WindowId, panel?: PanelId): void {
  try {
    opened.get(id)?.focus();
  } catch {
    // A closed window's handle.
  }
  bus.post({type: "focus", to: id, ...(panel ? {panelId: panel} : {})});
}

const subscribeBus = (cb: () => void) => windowBus().subscribe(cb);
const busVersion = () => windowBus().version();

/** The other live windows, re-rendering when one comes, goes or changes. */
export function useOpenWindows(): readonly WindowInfo[] {
  useSyncExternalStore(subscribeBus, busVersion, busVersion);
  return windowBus().windows();
}

/** This window's own presence (its label), re-rendering on change. */
export function useThisWindow(): WindowInfo {
  useSyncExternalStore(subscribeBus, busVersion, busVersion);
  return windowBus().self();
}

/** Is the main window live (always, in the main window). */
export function useMainWindowLive(): boolean {
  useSyncExternalStore(subscribeBus, busVersion, busVersion);
  return mainWindowLive(windowBus());
}

// ---------------------------------------------------------------------------
// Opening windows and moving panels to them.
// ---------------------------------------------------------------------------

/** The window ids in use or remembered: a new window takes another. */
function takenIds(bus: WindowBus, storage = browserStorage()): WindowId[] {
  const rec = readDashboard(parseJson(read(storage, dashboardKey())));
  // A previous dashboard's windows (offered for reopening) take their ids back: a new window must not collide.
  const prev = lastDashboardSession(storage);
  if (prev && prev !== dashboardSession()) rec.windows.push(...readDashboard(parseJson(read(storage, dashboardKey(prev)))).windows);
  return [bus.id, ...bus.windows().map((w) => w.id), ...rec.windows.map((w) => w.id)];
}

const parseJson = (raw: string | null): unknown => {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

/** Open pop-out `id` (focusing it when open), adding `panel` to it. False when the browser blocked it. */
export function openPopoutWindow(id: WindowId, panel?: PanelId): boolean {
  const handle = openWindow(id, panel, dashboardSession());
  if (!handle) return false;
  opened.set(id, handle);
  return true;
}

/** Open a new pop-out window holding `panel`. The new window's id, or null when the browser blocked it. */
export function openNewWindow(bus: WindowBus, panel?: PanelId): WindowId | null {
  const {id, label} = nextWindowId(takenIds(bus));
  storeWindowLabel(id, label);
  return openPopoutWindow(id, panel) ? id : null;
}

/** How a window takes a panel out, and puts it back. */
export interface PanelHandover {
  remove(): void;
  restore(): void;
}

/**
 * "Move to window": the other live windows (the main one first) and "New
 * window". A move hands the panel over (`sendPanel`); a new window takes it from
 * its URL. Offered for panels that may go to a pop-out window; for the main
 * window as a target, for any panel.
 */
export function moveToWindowItem(panel: RegisteredPanel, handover: PanelHandover, bus: WindowBus = windowBus()): PanelMenuItem | null {
  const toPopout = canPopOut(panel);
  if (!toPopout && bus.self().main) return null;
  const items = (): PanelMenuItem[] => {
    const out: PanelMenuItem[] = bus
      .windows()
      .filter((w) => !w.leaving && (w.main || toPopout))
      .map((w) => ({
        id: `window-${w.id}`,
        label: w.label,
        run: () => {
          void sendPanel(bus, panel.id, w.id, handover).then((ok) => {
            if (!ok) console.warn(`[windows] "${w.label}" did not take "${panel.id}"; it stays here`);
          });
        },
      }));
    const closed = bus.self().main ? closedWindows() : [];
    if (closed.length) {
      out.push({id: "window-reopen", label: `Reopen closed windows (${closed.length})`, run: () => void reopenWindows()});
    }
    if (toPopout) {
      out.push({
        id: "window-new",
        label: "New window",
        run: () => {
          if (openNewWindow(bus, panel.id)) handover.remove();
          else console.warn(`[windows] the browser blocked the new window for "${panel.id}"`);
        },
      });
    }
    return out;
  };
  return {id: "to-window", label: "Move to window", run: () => {}, items};
}

// ---------------------------------------------------------------------------
// The main window.
// ---------------------------------------------------------------------------

export interface MainWindowDeps {
  reveal(id: PanelId): boolean;
  hide(id: PanelId): boolean;
  isOpen(id: PanelId): boolean;
  /** The panels whose open state to watch (default: the registry's). */
  panels?: () => readonly RegisteredPanel[];
  storage?: KeyValueStorage | null;
  /** Open pop-out `id` (default `openPopoutWindow`). */
  open?: (id: WindowId) => boolean;
  /** Register the "Reopen windows" command (default true). */
  command?: boolean;
  /** This dashboard's session (default `dashboardSession()`). */
  session?: string;
  /** The channel of a session, to ask whether its main window is live (default: the `BroadcastChannel`). */
  channel?: (session: string) => WindowBusTransport;
  /** How long to wait for a live main window's answer, in ms. */
  claimWaitMs?: number;
  /** The clock for session bookkeeping. */
  now?: () => number;
}

export const REOPEN_WINDOWS_COMMAND = "core:reopen-windows";

/**
 * The main window's part of the dashboard. Returns the uninstall.
 *
 *   * A panel another window sends is revealed here (its binding, its place).
 *   * A panel another window shows is hidden here; revealing it here brings
 *     that window forward instead, and so does opening it here by any other
 *     means (a store flag set directly).
 *   * The panels of a window that closes (or falls silent) come back here,
 *     unless another window shows them or they are open already.
 *   * Which windows exist, with their panels, is recorded; a window closed while
 *     this one watched is forgotten (and its saved arrangement with it), so
 *     "Reopen windows" brings back what was open when this page last saw it.
 *   * All of it is keyed by this window's dashboard SESSION. The session whose
 *     dashboard was recorded last is remembered; a new session (a browser
 *     restart drops the old id) with no windows of its own offers to reopen that
 *     dashboard's windows, unless its main window is still live, and reopening
 *     ADOPTS them: their arrangements and names are copied into this session.
 *     Old sessions are pruned (`pruneSessions`).
 */
export function installMainWindow(bus: WindowBus, deps: MainWindowDeps): () => void {
  const storage = deps.storage === undefined ? browserStorage() : deps.storage;
  const panels = deps.panels ?? (() => listPanels());
  const session = deps.session ?? dashboardSession();
  const shellId = shell();
  const clock = deps.now ?? (() => Date.now());
  const channel = deps.channel ?? ((s: string) => broadcastTransport(windowBusChannelName(pagePath(), shellId, s)));
  let record: DashboardRecord = readDashboard(parseJson(read(storage, dashboardKey(session))));
  const previous = lastDashboardSession(storage);
  touchSession(storage, session, clock());
  pruneSessions(storage, {keep: [session, previous], now: clock()});
  const save = (next: DashboardRecord) => {
    if (next === record) return;
    record = next;
    write(storage, dashboardKey(session), JSON.stringify(record));
    if (record.windows.length) {
      write(storage, lastDashboardKey(shellId), JSON.stringify({v: 1, session}));
      touchSession(storage, session, clock());
    }
  };

  /** The previous session's windows, once known to be free to adopt. */
  let adoptable: DashboardWindow[] = [];
  let live = true;
  const probed = (async () => {
    if (!previous || previous === session || record.windows.length) return;
    const windows = readDashboard(parseJson(read(storage, dashboardKey(previous)))).windows;
    if (!windows.length) return;
    const free = await claimSession(channel(previous), {waitMs: deps.claimWaitMs});
    if (free && live) adoptable = windows;
  })();
  /** Not yet in this dashboard and not open. */
  const adoptableNow = () => adoptable.filter((w) => !record.windows.some((x) => x.id === w.id) && !bus.windows().some((x) => x.id === w.id));
  const adopt = () => {
    const from = previous!;
    let next = record;
    for (const w of adoptableNow()) {
      const layout = read(storage, windowLayoutKey(w.id, from, shellId));
      if (layout !== null) write(storage, windowLayoutKey(w.id, session, shellId), layout);
      const label = read(storage, windowLabelKey(w.id, from, shellId));
      if (label !== null) write(storage, windowLabelKey(w.id, session, shellId), label);
      next = recordWindow(next, w);
    }
    adoptable = [];
    save(next);
  };
  /** A window whose arriving panel is being docked here: its own presence may still list it. */
  let arrivingFrom: WindowId | null = null;
  const owner = (id: PanelId) => bus.windows().find((w) => w.id !== arrivingFrom && w.panels.includes(id));

  const offs: Array<() => void> = [];
  offs.push(
    acceptPanels(bus, (id, from) => {
      arrivingFrom = from;
      try {
        return deps.reveal(id);
      } finally {
        arrivingFrom = null;
      }
    }),
  );
  offs.push(
    bus.on("reveal", (msg) => {
      if (msg.to === bus.id) deps.reveal(msg.panelId);
    }),
  );
  setRevealElsewhere((id) => {
    const w = owner(id);
    if (!w) return false;
    focusWindow(bus, w.id, id);
    return true;
  });

  /** Hide here what another window shows; `focus`: and bring that window forward (it was just opened here). */
  const enforce = (focus: boolean) => {
    for (const w of bus.windows()) {
      for (const id of w.panels) {
        if (!deps.isOpen(id)) continue;
        deps.hide(id);
        if (focus) focusWindow(bus, w.id, id);
      }
    }
  };
  const onWindows = () => {
    setPlacedInOtherWindows(bus.windows().flatMap((w) => w.panels));
    let next = record;
    for (const w of bus.windows()) next = recordWindow(next, w);
    save(next);
    enforce(false);
  };
  offs.push(bus.subscribe(onWindows));
  offs.push(subscribePanels(panels(), () => enforce(true)));
  offs.push(
    bus.onGone((info, reason) => {
      for (const id of panelsToReturn(info.panels, bus.windows(), deps.isOpen)) deps.reveal(id);
      if (reason === "closed") {
        save(forgetWindow(record, info.id));
        write(storage, windowLayoutKey(info.id, session, shellId), null);
        write(storage, windowLabelKey(info.id, session, shellId), null);
      }
    }),
  );
  onWindows();

  const closed = () => [...closedDashboardWindows(record, bus.windows()), ...adoptableNow()];
  if (deps.command !== false) {
    registerCommand(
      {
        id: REOPEN_WINDOWS_COMMAND,
        label: "Reopen windows",
        group: "Window",
        when: () => closed().length > 0,
        run: () => void reopenWindows(),
      },
      "core",
    );
  }
  const open = deps.open ?? ((id: WindowId) => openPopoutWindow(id));
  /** Reopen the recorded windows that are closed (adopting the previous dashboard's first), until the browser blocks one. The ids opened. */
  const reopenWindows = (): WindowId[] => {
    if (adoptableNow().length) adopt();
    const done: WindowId[] = [];
    for (const w of closed()) {
      if (!open(w.id)) break;
      done.push(w.id);
    }
    return done;
  };
  installed = {reopen: reopenWindows, closed, probed};

  return () => {
    live = false;
    for (const off of offs) off();
    setRevealElsewhere(null);
    setPlacedInOtherWindows([]);
    if (deps.command !== false) unregisterCommand(REOPEN_WINDOWS_COMMAND);
    installed = null;
  };
}

let installed: {reopen(): WindowId[]; closed(): ReturnType<typeof closedDashboardWindows>; probed: Promise<void>} | null = null;

/** Resolves once the main window has checked whether the previous dashboard's windows can be offered for reopening. */
export function dashboardProbed(): Promise<void> {
  return installed?.probed ?? Promise.resolve();
}

/** Reopen the dashboard's closed windows (from a click: browsers block windows opened without one). */
export function reopenWindows(): WindowId[] {
  return installed?.reopen() ?? [];
}

/** The dashboard's windows that are closed now (none outside the main window). */
export function closedWindows(): ReturnType<typeof closedDashboardWindows> {
  return installed?.closed() ?? [];
}
