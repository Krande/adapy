// The window bus: how the browser windows of one viewer session talk to each
// other, so several windows (on several monitors, say) form one dashboard.
//
//   * A TRANSPORT carries messages between the windows (`WindowBusTransport`).
//     In the browser that is a `BroadcastChannel`, same origin and same browser
//     only, scoped by the page's path and the active UI shell, so two shells or
//     two deployments on one origin never hear each other. An in-memory hub
//     serves tests. A relay over a websocket would implement the same interface.
//   * Every window has an ID: the main window is `MAIN_WINDOW_ID`; a pop-out
//     window carries its own in its URL (`?window=<id>`, see `./popout`), so a
//     reopened window finds its arrangement again. It also has a LABEL ("Main",
//     "Window 2", ...), which the user may change.
//   * PRESENCE: a window says hello when it starts, announces itself (label and
//     the panels it shows) on every change and on a heartbeat, and says goodbye
//     when the page goes away. A window that stops announcing is dropped after
//     `STALE_MS`; one that said goodbye is dropped after `LEAVE_GRACE_MS`, unless
//     it comes straight back (a reload).
//   * MOVING a panel between windows is a handshake (`sendPanel` /
//     `acceptPanels`): the source takes the panel out, asks the target to dock
//     it, and puts it back when no acknowledgement arrives in time.
//
// Dependency-free (no DOM at import, no store), so the facade root can export
// it and `node --test` covers it directly.

import type { PanelId } from "./panel";

/** A window's id: `MAIN_WINDOW_ID`, or a pop-out's own. */
export type WindowId = string;

/** The id of the main window (the one with the 3D view). */
export const MAIN_WINDOW_ID: WindowId = "main";
/** The main window's label. */
export const MAIN_WINDOW_LABEL = "Main";

/** A heartbeat this often, in ms. */
export const HEARTBEAT_MS = 3000;
/** A window silent this long is gone, in ms (a few missed heartbeats; background windows tick slower). */
export const STALE_MS = 15000;
/** A window that said goodbye is gone after this long, unless it announces itself again (a reload), in ms. */
export const LEAVE_GRACE_MS = 1500;
/** How long a moved panel waits for the target's acknowledgement before it goes back, in ms. */
export const MOVE_ACK_MS = 1500;

// ---------------------------------------------------------------------------
// Messages.
// ---------------------------------------------------------------------------

/** What a window says about itself. */
export interface WindowPresence {
  label: string;
  /** The panels it shows. */
  panels: PanelId[];
  /** It is the main window. */
  main: boolean;
}

export type WindowBusMessage =
  /** A window starting: the others answer with `announce`. */
  | ({ type: "hello"; from: WindowId } & WindowPresence)
  /** A window's presence: on every change and on a heartbeat. */
  | ({ type: "announce"; from: WindowId } & WindowPresence)
  /** A window going away, with the panels it showed. */
  | { type: "goodbye"; from: WindowId; panels: PanelId[] }
  /** Dock `panelId` in window `to`; answered with `dock-ack`. */
  | { type: "dock-panel"; from: WindowId; to: WindowId; panelId: PanelId; moveId: string }
  | { type: "dock-ack"; from: WindowId; to: WindowId; moveId: string; ok: boolean }
  /** Bring window `to` to the front, on `panelId` when given. */
  | { type: "focus"; from: WindowId; to: WindowId; panelId?: PanelId }
  /** Reveal `panelId` in window `to` (a pop-out asking the main window). */
  | { type: "reveal"; from: WindowId; to: WindowId; panelId: PanelId }
  /** A snapshot of state the main window shares (`./windowMirror`). */
  | { type: "mirror"; from: WindowId; topic: string; data: unknown; seq: number }
  /** Ask for the current snapshots (of `topics`, else all). */
  | { type: "mirror-request"; from: WindowId; topics?: string[] }
  /** An action for window `to` to run on its own state (`./windowMirror`). */
  | { type: "command"; from: WindowId; to: WindowId; name: string; args: unknown };

export type WindowBusMessageType = WindowBusMessage["type"];

/** A message as a window sends it: the bus fills in `from`. */
export type OutgoingWindowMessage = WindowBusMessage extends infer M ? (M extends { from: WindowId } ? Omit<M, "from"> : never) : never;

const TYPES: ReadonlySet<string> = new Set<WindowBusMessageType>([
  "hello",
  "announce",
  "goodbye",
  "dock-panel",
  "dock-ack",
  "focus",
  "reveal",
  "mirror",
  "mirror-request",
  "command",
]);

/** Is `raw` a bus message (anything else on the channel is ignored). */
export function isWindowBusMessage(raw: unknown): raw is WindowBusMessage {
  if (!raw || typeof raw !== "object") return false;
  const m = raw as Record<string, unknown>;
  return typeof m.type === "string" && TYPES.has(m.type) && typeof m.from === "string";
}

// ---------------------------------------------------------------------------
// Transports.
// ---------------------------------------------------------------------------

/** What carries messages between windows. A window does not hear its own posts. */
export interface WindowBusTransport {
  post(msg: WindowBusMessage): void;
  /** Returns the unsubscribe. */
  subscribe(cb: (msg: WindowBusMessage) => void): () => void;
  close(): void;
}

/** A transport that carries nothing: where there is no `BroadcastChannel` (SSR, tests). */
export const nullTransport: WindowBusTransport = {
  post() {},
  subscribe: () => () => {},
  close() {},
};

/** The channel name for the windows of one page path and UI shell. */
export function windowBusChannelName(pathname: string, shellId: string): string {
  return `ada-windows:${pathname || "/"}:${shellId}`;
}

/** A `BroadcastChannel` transport; `nullTransport` where the browser has none. */
export function broadcastTransport(name: string): WindowBusTransport {
  if (typeof BroadcastChannel === "undefined") return nullTransport;
  let channel: BroadcastChannel | null;
  try {
    channel = new BroadcastChannel(name);
    // Node's channel holds the process open; a browser's has no `unref`.
    (channel as { unref?: () => void }).unref?.();
  } catch {
    return nullTransport;
  }
  return {
    post(msg) {
      try {
        channel?.postMessage(msg);
      } catch (err) {
        // A message that cannot be cloned is dropped, not thrown into the caller.
        console.warn("[windows] a message could not be sent", err);
      }
    },
    subscribe(cb) {
      const c = channel;
      if (!c) return () => {};
      const listener = (e: MessageEvent) => {
        if (isWindowBusMessage(e.data)) cb(e.data);
      };
      c.addEventListener("message", listener);
      return () => c.removeEventListener("message", listener);
    },
    close() {
      channel?.close();
      channel = null;
    },
  };
}

/**
 * Transports joined in memory, for tests: a post reaches every other transport
 * of the hub (synchronously, a structured copy), never its own.
 */
export function memoryTransportHub(): { connect(): WindowBusTransport } {
  const members = new Set<{ cbs: Set<(msg: WindowBusMessage) => void> }>();
  const copy = <T>(v: T): T => (typeof structuredClone === "function" ? structuredClone(v) : (JSON.parse(JSON.stringify(v)) as T));
  return {
    connect() {
      const me = { cbs: new Set<(msg: WindowBusMessage) => void>() };
      members.add(me);
      return {
        post(msg) {
          if (!members.has(me)) return;
          for (const m of [...members]) {
            if (m === me) continue;
            for (const cb of [...m.cbs]) cb(copy(msg));
          }
        },
        subscribe(cb) {
          me.cbs.add(cb);
          return () => me.cbs.delete(cb);
        },
        close() {
          members.delete(me);
          me.cbs.clear();
        },
      };
    },
  };
}

// ---------------------------------------------------------------------------
// The bus: presence over a transport.
// ---------------------------------------------------------------------------

/** Another window as this one knows it. */
export interface WindowInfo extends WindowPresence {
  id: WindowId;
  /** When it was last heard from (the bus's clock). */
  seen: number;
  /** It said goodbye and is waiting out `LEAVE_GRACE_MS`. */
  leaving?: boolean;
}

/** Why a window was dropped: it said goodbye (and did not come back), or it fell silent. */
export type WindowGoneReason = "closed" | "lost";

export interface WindowBusOptions {
  id: WindowId;
  label: string;
  /** This is the main window. Default: `id === MAIN_WINDOW_ID`. */
  main?: boolean;
  transport: WindowBusTransport;
  /** The clock; `Date.now` by default. */
  now?: () => number;
  heartbeatMs?: number;
  staleMs?: number;
  leaveGraceMs?: number;
  /** Run the heartbeat on a timer of its own after `start` (default true; tests call `tick`). */
  timers?: boolean;
}

export interface WindowBus {
  readonly id: WindowId;
  /** Send a message to the other windows (`from` is this window). */
  post(msg: OutgoingWindowMessage): void;
  /** Handle messages of one type. Returns the unsubscribe. */
  on<T extends WindowBusMessageType>(type: T, handler: (msg: Extract<WindowBusMessage, { type: T }>) => void): () => void;
  /** The other windows that are live, the main window first, then by label. */
  windows(): readonly WindowInfo[];
  /** This window's own presence. */
  self(): WindowInfo;
  /** Notifies when `windows()` or `self()` may have changed. Returns the unsubscribe. */
  subscribe(cb: () => void): () => void;
  /** Changes whenever `windows()` or `self()` does (for `useSyncExternalStore`). */
  version(): number;
  /** The panels this window shows; announced when they change. */
  setPanels(panels: readonly PanelId[]): void;
  setLabel(label: string): void;
  /** Called with a window that was dropped and why. Returns the unsubscribe. */
  onGone(cb: (info: WindowInfo, reason: WindowGoneReason) => void): () => void;
  /** Say hello and start the heartbeat. */
  start(): void;
  /** One heartbeat: announce, and drop the windows gone silent or gone. */
  tick(): void;
  /** Say goodbye and stop. */
  stop(): void;
  readonly started: boolean;
}

const sameList = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export function createWindowBus(opts: WindowBusOptions): WindowBus {
  const now = opts.now ?? (() => Date.now());
  const heartbeat = opts.heartbeatMs ?? HEARTBEAT_MS;
  const stale = opts.staleMs ?? STALE_MS;
  const grace = opts.leaveGraceMs ?? LEAVE_GRACE_MS;
  const transport = opts.transport;
  const me: WindowInfo = { id: opts.id, label: opts.label, panels: [], main: opts.main ?? opts.id === MAIN_WINDOW_ID, seen: now() };
  const others = new Map<WindowId, WindowInfo>();
  const handlers = new Map<string, Set<(msg: WindowBusMessage) => void>>();
  const listeners = new Set<() => void>();
  const gone = new Set<(info: WindowInfo, reason: WindowGoneReason) => void>();
  let version = 0;
  let started = false;
  let timer: ReturnType<typeof setInterval> | null = null;
  let unsubscribe: (() => void) | null = null;
  let sorted: WindowInfo[] = [];

  const changed = () => {
    version++;
    sorted = [...others.values()]
      .filter((w) => !w.leaving)
      .sort((a, b) => Number(b.main) - Number(a.main) || a.label.localeCompare(b.label, undefined, { numeric: true }) || a.id.localeCompare(b.id));
    for (const l of [...listeners]) l();
  };

  const presence = (): WindowPresence => ({ label: me.label, panels: [...me.panels], main: me.main });
  const send = (msg: OutgoingWindowMessage) => {
    if (started) transport.post({ ...msg, from: me.id } as WindowBusMessage);
  };

  const drop = (id: WindowId, reason: WindowGoneReason) => {
    const info = others.get(id);
    if (!info) return;
    others.delete(id);
    changed();
    for (const cb of [...gone]) cb(info, reason);
  };

  const seen = (msg: Extract<WindowBusMessage, { type: "hello" | "announce" }>) => {
    const had = others.get(msg.from);
    const next: WindowInfo = { id: msg.from, label: msg.label, panels: [...msg.panels], main: !!msg.main, seen: now() };
    others.set(msg.from, next);
    if (!had || had.leaving || had.label !== next.label || had.main !== next.main || !sameList(had.panels, next.panels)) changed();
  };

  const receive = (msg: WindowBusMessage) => {
    if (msg.from === me.id) return;
    // Anything from a window is a sign of life.
    const known = others.get(msg.from);
    if (known && msg.type !== "goodbye") known.seen = now();
    switch (msg.type) {
      case "hello":
        seen(msg);
        send({ type: "announce", ...presence() });
        break;
      case "announce":
        seen(msg);
        break;
      case "goodbye":
        if (known) {
          others.set(msg.from, { ...known, panels: [...msg.panels], leaving: true, seen: now() });
          changed();
        }
        break;
      default:
        break;
    }
    const hs = handlers.get(msg.type);
    if (hs) for (const h of [...hs]) h(msg);
  };

  const bus: WindowBus = {
    get id() {
      return me.id;
    },
    get started() {
      return started;
    },
    post: send,
    on(type, handler) {
      let hs = handlers.get(type);
      if (!hs) handlers.set(type, (hs = new Set()));
      const h = handler as (msg: WindowBusMessage) => void;
      hs.add(h);
      return () => void hs!.delete(h);
    },
    windows: () => sorted,
    self: () => me,
    subscribe(cb) {
      listeners.add(cb);
      return () => void listeners.delete(cb);
    },
    version: () => version,
    setPanels(panels) {
      if (sameList(me.panels, panels)) return;
      me.panels = [...panels];
      changed();
      send({ type: "announce", ...presence() });
    },
    setLabel(label) {
      const l = label.trim();
      if (!l || l === me.label) return;
      me.label = l;
      changed();
      send({ type: "announce", ...presence() });
    },
    onGone(cb) {
      gone.add(cb);
      return () => void gone.delete(cb);
    },
    start() {
      if (started) return;
      started = true;
      unsubscribe = transport.subscribe(receive);
      send({ type: "hello", ...presence() });
      if (opts.timers !== false && typeof setInterval === "function") {
        timer = setInterval(() => bus.tick(), heartbeat);
        // Under node, a heartbeat alone must not keep the process alive.
        (timer as { unref?: () => void }).unref?.();
      }
    },
    tick() {
      if (!started) return;
      send({ type: "announce", ...presence() });
      const t = now();
      for (const w of [...others.values()]) {
        if (w.leaving && t - w.seen >= grace) drop(w.id, "closed");
        else if (!w.leaving && t - w.seen >= stale) drop(w.id, "lost");
      }
    },
    stop() {
      if (!started) return;
      send({ type: "goodbye", panels: [...me.panels] });
      started = false;
      if (timer) clearInterval(timer);
      timer = null;
      unsubscribe?.();
      unsubscribe = null;
    },
  };
  return bus;
}

/** The live window of `id` (this one included), or undefined. */
export function findWindow(bus: Pick<WindowBus, "windows" | "self">, id: WindowId): WindowInfo | undefined {
  return id === bus.self().id ? bus.self() : bus.windows().find((w) => w.id === id);
}

/** The live window other than this one that shows `panel`, if any. */
export function windowShowing(bus: Pick<WindowBus, "windows">, panel: PanelId): WindowInfo | undefined {
  return bus.windows().find((w) => w.panels.includes(panel));
}

/** Is the main window live (seen from a pop-out; always true in the main window). */
export function mainWindowLive(bus: Pick<WindowBus, "windows" | "self">): boolean {
  return bus.self().main || bus.windows().some((w) => w.main);
}

// ---------------------------------------------------------------------------
// Moving a panel between windows.
// ---------------------------------------------------------------------------

export interface SendPanelOptions {
  /** Take the panel out of this window. Called before the request goes out. */
  remove(): void;
  /** Put it back: the target refused, or did not answer in time. */
  restore(): void;
  timeoutMs?: number;
  /** Timers, for tests. */
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

let moveSeq = 0;

/**
 * Move `panelId` to window `to`: take it out here, ask the target to dock it,
 * and resolve true on its acknowledgement; on a refusal or no answer within
 * `timeoutMs`, put it back and resolve false.
 */
export function sendPanel(bus: WindowBus, panelId: PanelId, to: WindowId, opts: SendPanelOptions): Promise<boolean> {
  const setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  const moveId = `${bus.id}:${++moveSeq}:${Math.random().toString(36).slice(2, 8)}`;
  return new Promise<boolean>((resolve) => {
    let done = false;
    let handle: unknown = null;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      off();
      if (handle !== null) clearTimer(handle);
      if (!ok) opts.restore();
      resolve(ok);
    };
    // Listening before the request goes out: a transport may answer synchronously.
    const off = bus.on("dock-ack", (msg) => {
      if (msg.to === bus.id && msg.moveId === moveId) finish(msg.ok);
    });
    opts.remove();
    handle = setTimer(() => finish(false), opts.timeoutMs ?? MOVE_ACK_MS);
    bus.post({ type: "dock-panel", to, panelId, moveId });
  });
}

/**
 * Take the panels other windows send here: `dock` places one (true when it did)
 * and the sender is told. Returns the unsubscribe.
 */
export function acceptPanels(bus: WindowBus, dock: (panelId: PanelId, from: WindowId) => boolean): () => void {
  return bus.on("dock-panel", (msg) => {
    if (msg.to !== bus.id) return;
    let ok = false;
    try {
      ok = dock(msg.panelId, msg.from);
    } catch (err) {
      console.warn(`[windows] docking "${msg.panelId}" failed`, err);
    }
    bus.post({ type: "dock-ack", to: msg.from, moveId: msg.moveId, ok });
  });
}

/**
 * The panels a window that went away showed that should come back to the main
 * window: not shown by another live window, and not already open here.
 */
export function panelsToReturn(
  gonePanels: readonly PanelId[],
  live: readonly Pick<WindowInfo, "panels">[],
  openHere: (id: PanelId) => boolean,
): PanelId[] {
  const elsewhere = new Set(live.flatMap((w) => w.panels));
  return [...new Set(gonePanels)].filter((id) => !elsewhere.has(id) && !openHere(id));
}

// ---------------------------------------------------------------------------
// Window ids and labels.
// ---------------------------------------------------------------------------

/** A pop-out window's label from its number: "Window 2". */
export const windowLabel = (n: number): string => `Window ${n}`;

/**
 * A fresh pop-out id, `w<n>`, with the least `n` from 2 not used by `taken`
 * (live windows and remembered ones), and its default label.
 */
export function nextWindowId(taken: Iterable<WindowId>): { id: WindowId; label: string } {
  const used = new Set(taken);
  let n = 2;
  while (used.has(`w${n}`)) n++;
  return { id: `w${n}`, label: windowLabel(n) };
}

/** The default label of a window id: "Main", "Window <n>" for `w<n>`, else the id. */
export function defaultWindowLabel(id: WindowId): string {
  if (id === MAIN_WINDOW_ID) return MAIN_WINDOW_LABEL;
  const m = /^w(\d+)$/.exec(id);
  return m ? windowLabel(Number(m[1])) : id;
}

/** Is `id` usable as a pop-out window's id (it goes into URLs and storage keys). */
export function isWindowId(id: unknown): id is WindowId {
  return typeof id === "string" && id !== MAIN_WINDOW_ID && /^[A-Za-z0-9_-]{1,40}$/.test(id);
}

// ---------------------------------------------------------------------------
// The dashboard: which windows the main window last knew, so they can be reopened.
// ---------------------------------------------------------------------------

export interface DashboardWindow {
  id: WindowId;
  label: string;
  panels: PanelId[];
}

export interface DashboardRecord {
  v: 1;
  windows: DashboardWindow[];
}

export const emptyDashboard = (): DashboardRecord => ({ v: 1, windows: [] });

/** A window's presence recorded (or updated). Unchanged when it already says the same. */
export function recordWindow(rec: DashboardRecord, w: Pick<WindowInfo, "id" | "label" | "panels">): DashboardRecord {
  const at = rec.windows.findIndex((x) => x.id === w.id);
  const entry: DashboardWindow = { id: w.id, label: w.label, panels: [...w.panels] };
  if (at >= 0) {
    const had = rec.windows[at];
    if (had.label === entry.label && sameList(had.panels, entry.panels)) return rec;
    const windows = [...rec.windows];
    windows[at] = entry;
    return { ...rec, windows };
  }
  return { ...rec, windows: [...rec.windows, entry] };
}

/** A window closed for good: out of the record. */
export function forgetWindow(rec: DashboardRecord, id: WindowId): DashboardRecord {
  return rec.windows.some((w) => w.id === id) ? { ...rec, windows: rec.windows.filter((w) => w.id !== id) } : rec;
}

/** A stored record made safe to use. */
export function readDashboard(raw: unknown): DashboardRecord {
  if (!raw || typeof raw !== "object") return emptyDashboard();
  const r = raw as Partial<DashboardRecord>;
  if (r.v !== 1 || !Array.isArray(r.windows)) return emptyDashboard();
  const windows: DashboardWindow[] = [];
  for (const w of r.windows) {
    if (!w || typeof w !== "object" || !isWindowId((w as DashboardWindow).id)) continue;
    const x = w as DashboardWindow;
    if (windows.some((y) => y.id === x.id)) continue;
    windows.push({
      id: x.id,
      label: typeof x.label === "string" && x.label ? x.label : defaultWindowLabel(x.id),
      panels: Array.isArray(x.panels) ? x.panels.filter((p): p is string => typeof p === "string") : [],
    });
  }
  return { v: 1, windows };
}

/** The recorded windows that are not open now. */
export function closedDashboardWindows(rec: DashboardRecord, live: readonly Pick<WindowInfo, "id">[]): DashboardWindow[] {
  const open = new Set(live.map((w) => w.id));
  return rec.windows.filter((w) => !open.has(w.id));
}
