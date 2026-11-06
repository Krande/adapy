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

import {activeUiShellId} from "@/plugins/uiShells";

import {registerCommand, unregisterCommand} from "./commands";
import type {PanelId, PanelMenuItem, RegisteredPanel} from "./panel";
import {setPlacedInOtherWindows} from "./placedElsewhere";
import {canPopOut, openWindow, popoutPanelId, popoutWindowId} from "./popout";
import {listPanels} from "./registry";
import {setRevealElsewhere} from "./reveal";
import {subscribePanels} from "./panelStates";
import {
  MAIN_WINDOW_ID,
  MAIN_WINDOW_LABEL,
  acceptPanels,
  broadcastTransport,
  closedDashboardWindows,
  createWindowBus,
  defaultWindowLabel,
  forgetWindow,
  mainWindowLive,
  nextWindowId,
  panelsToReturn,
  readDashboard,
  recordWindow,
  sendPanel,
  windowBusChannelName,
  type DashboardRecord,
  type WindowBus,
  type WindowId,
  type WindowInfo,
} from "./windowBus";

// ---------------------------------------------------------------------------
// Storage keys, per UI shell.
// ---------------------------------------------------------------------------

const shell = () => activeUiShellId();
/** A pop-out window's arrangement (its own `LayoutPrefs`, apart from the main window's). */
export const windowLayoutKey = (id: WindowId, shellId: string = shell()) => `ada:layout-window:${shellId}:${id}`;
/** A pop-out window's label as the user named it. */
export const windowLabelKey = (id: WindowId, shellId: string = shell()) => `ada:window-label:${shellId}:${id}`;
/** The main window's record of the dashboard's windows. */
export const dashboardKey = (shellId: string = shell()) => `ada:dashboard:${shellId}`;

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
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
export function storedWindowLabel(id: WindowId, storage = browserStorage()): string | null {
  return read(storage, windowLabelKey(id));
}

export function storeWindowLabel(id: WindowId, label: string, storage = browserStorage()): void {
  write(storage, windowLabelKey(id), label.trim() || null);
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

let pageBus: WindowBus | null = null;
/** Pop-out windows this page opened, by id: focusing one through its handle works where `focus()` from inside does not. */
const opened = new Map<WindowId, Window>();

/** This page's bus, started on first use (a no-op transport where there is no `BroadcastChannel`). */
export function windowBus(): WindowBus {
  if (pageBus) return pageBus;
  const id = thisWindowId();
  const path = typeof window !== "undefined" ? window.location.pathname : "/";
  const label = id === MAIN_WINDOW_ID ? MAIN_WINDOW_LABEL : (storedWindowLabel(id) ?? defaultWindowLabel(id));
  const bus = createWindowBus({id, label, transport: broadcastTransport(windowBusChannelName(path, shell())), timers: typeof window !== "undefined"});
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
  const handle = openWindow(id, panel);
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
 */
export function installMainWindow(bus: WindowBus, deps: MainWindowDeps): () => void {
  const storage = deps.storage === undefined ? browserStorage() : deps.storage;
  const panels = deps.panels ?? (() => listPanels());
  let record: DashboardRecord = readDashboard(parseJson(read(storage, dashboardKey())));
  const save = (next: DashboardRecord) => {
    if (next === record) return;
    record = next;
    write(storage, dashboardKey(), JSON.stringify(record));
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
        write(storage, windowLayoutKey(info.id), null);
        write(storage, windowLabelKey(info.id), null);
      }
    }),
  );
  onWindows();

  const closed = () => closedDashboardWindows(record, bus.windows());
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
  /** Reopen the recorded windows that are closed, until the browser blocks one. The ids opened. */
  const reopenWindows = (): WindowId[] => {
    const done: WindowId[] = [];
    for (const w of closed()) {
      if (!open(w.id)) break;
      done.push(w.id);
    }
    return done;
  };
  installed = {reopen: reopenWindows, closed};

  return () => {
    for (const off of offs) off();
    setRevealElsewhere(null);
    setPlacedInOtherWindows([]);
    if (deps.command !== false) unregisterCommand(REOPEN_WINDOWS_COMMAND);
    installed = null;
  };
}

let installed: {reopen(): WindowId[]; closed(): ReturnType<typeof closedDashboardWindows>} | null = null;

/** Reopen the dashboard's closed windows (from a click: browsers block windows opened without one). */
export function reopenWindows(): WindowId[] {
  return installed?.reopen() ?? [];
}

/** The dashboard's windows that are closed now (none outside the main window). */
export function closedWindows(): ReturnType<typeof closedDashboardWindows> {
  return installed?.closed() ?? [];
}
