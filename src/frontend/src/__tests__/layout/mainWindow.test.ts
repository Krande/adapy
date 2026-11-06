// The main window of a multi-window dashboard: panels sent to it, panels other
// windows show hidden here (and revealed there), a closed window's panels taken
// back, and the dashboard record behind "Reopen windows".

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { createWindowBus, memoryTransportHub, MAIN_WINDOW_ID, sendPanel } = await import("@/layout/windowBus");
const { installMainWindow, dashboardKey, windowLayoutKey, REOPEN_WINDOWS_COMMAND } = await import("@/layout/windows");
const { reveal } = await import("@/layout/reveal");
const { getCommand, runCommand } = await import("@/layout/commands");
const { isPlacedElsewhere } = await import("@/layout/placedElsewhere");

function memStorage() {
  const m = new Map<string, string>();
  return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
}

function setup(opts: { storage?: ReturnType<typeof memStorage> } = {}) {
  const hub = memoryTransportHub();
  const c = { t: 1000 };
  const now = () => c.t;
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), now, timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now, timers: false });
  const open = new Set<string>();
  const log: string[] = [];
  const storage = opts.storage ?? memStorage();
  const opened: string[] = [];
  let allowOpen = 1;
  main.start();
  const uninstall = installMainWindow(main, {
    reveal: (id) => (log.push(`reveal ${id}`), open.add(id), true),
    hide: (id) => (log.push(`hide ${id}`), open.delete(id), true),
    isOpen: (id) => open.has(id),
    panels: () => [],
    storage,
    open: (id) => (allowOpen-- > 0 ? (opened.push(id), true) : false),
  });
  return { hub, c, now, main, w2, open, log, storage, opened, uninstall, allow: (n: number) => (allowOpen = n) };
}

test("a panel sent to the main window is revealed here and acknowledged", async () => {
  const s = setup();
  s.w2.start();
  const ok = await sendPanel(s.w2, "core:admin", MAIN_WINDOW_ID, { remove() {}, restore: () => assert.fail("no restore"), timeoutMs: 50 });
  assert.equal(ok, true);
  assert.deepEqual(s.log, ["reveal core:admin"]);
  s.uninstall();
});

test("a panel another window shows is hidden here, left to region hosts, and revealed there instead", () => {
  const s = setup();
  s.open.add("demo:a");
  const focus: string[] = [];
  s.w2.on("focus", (m) => focus.push(`${m.to}:${m.panelId}`));
  s.w2.start();
  s.w2.setPanels(["demo:a"]);
  assert.equal(s.open.has("demo:a"), false, "hidden in the main window");
  assert.equal(isPlacedElsewhere("demo:a"), true);
  assert.equal(reveal("demo:a"), true);
  assert.deepEqual(focus, ["w2:demo:a"], "the other window is asked to come forward");
  assert.equal(s.open.has("demo:a"), false, "not opened a second time here");
  s.uninstall();
  assert.equal(isPlacedElsewhere("demo:a"), false);
});

test("a window that closes gives its panels back, after the grace; its record and saved arrangement go", () => {
  const s = setup();
  s.storage.setItem(windowLayoutKey("w2"), "{}");
  s.w2.start();
  s.w2.setPanels(["demo:a", "demo:b"]);
  s.main.tick();
  assert.match(s.storage.getItem(dashboardKey()) ?? "", /"w2"/, "recorded");
  s.w2.stop();
  assert.deepEqual(s.log.filter((l) => l.startsWith("reveal")), [], "nothing back during the grace");
  // Opened here meanwhile: not revealed a second time.
  s.open.add("demo:b");
  s.c.t += 2000;
  s.main.tick();
  assert.deepEqual(s.log.filter((l) => l.startsWith("reveal")), ["reveal demo:a"]);
  assert.doesNotMatch(s.storage.getItem(dashboardKey()) ?? "", /"w2"/, "forgotten");
  assert.equal(s.storage.getItem(windowLayoutKey("w2")), null);
  s.uninstall();
});

test("a reload keeps the window: no panel comes back", () => {
  const s = setup();
  s.w2.start();
  s.w2.setPanels(["demo:a"]);
  s.w2.stop();
  const again = createWindowBus({ id: "w2", label: "Window 2", transport: s.hub.connect(), now: s.now, timers: false });
  again.start();
  again.setPanels(["demo:a"]);
  s.c.t += 2000;
  s.main.tick();
  assert.deepEqual(s.log.filter((l) => l.startsWith("reveal")), []);
  s.uninstall();
});

test("a window that falls silent gives its panels back but stays recorded; Reopen windows opens it", () => {
  const s = setup();
  s.w2.start();
  s.w2.setPanels(["demo:a"]);
  s.c.t += 20000;
  s.main.tick();
  assert.deepEqual(s.log.filter((l) => l.startsWith("reveal")), ["reveal demo:a"]);
  assert.match(s.storage.getItem(dashboardKey()) ?? "", /"w2"/);
  assert.ok(getCommand(REOPEN_WINDOWS_COMMAND));
  assert.equal(runCommand(REOPEN_WINDOWS_COMMAND), true);
  assert.deepEqual(s.opened, ["w2"]);
  s.uninstall();
  assert.equal(getCommand(REOPEN_WINDOWS_COMMAND), undefined);
});

test("Reopen windows is offered only while a recorded window is closed, and stops where the browser blocks", () => {
  const storage = memStorage();
  storage.setItem(
    dashboardKey(),
    JSON.stringify({ v: 1, windows: [{ id: "w2", label: "Window 2", panels: [] }, { id: "w3", label: "Window 3", panels: [] }] }),
  );
  const s = setup({ storage });
  s.allow(1);
  assert.equal(runCommand(REOPEN_WINDOWS_COMMAND), true);
  assert.deepEqual(s.opened, ["w2"], "the second is blocked: the next click opens it");
  s.allow(5);
  s.w2.start();
  runCommand(REOPEN_WINDOWS_COMMAND);
  assert.deepEqual(s.opened, ["w2", "w3"], "a window open now is not opened again");
  const w3 = createWindowBus({ id: "w3", label: "Window 3", transport: s.hub.connect(), now: s.now, timers: false });
  w3.start();
  assert.equal(runCommand(REOPEN_WINDOWS_COMMAND), false, "nothing closed: not offered");
  s.uninstall();
});

// ---------------------------------------------------------------------------
// Dashboard sessions: storage per session, adoption of the last dashboard, pruning.
// ---------------------------------------------------------------------------

const { windowLabelKey, lastDashboardKey, sessionsKey, dashboardProbed, pruneSessions, closedWindows, reopenWindows, touchSession, SESSION_KEEP } = await import(
  "@/layout/windows"
);
const { windowBusChannelName } = await import("@/layout/windowBus");

/** A storage that can be enumerated, as `localStorage` can. */
function listStorage() {
  const s = memStorage();
  return {
    ...s,
    get length() {
      return s.m.size;
    },
    key: (i: number) => [...s.m.keys()][i] ?? null,
  };
}
const rec = (...ids: string[]) => JSON.stringify({ v: 1, windows: ids.map((id) => ({ id, label: `Window ${id.slice(1)}`, panels: [] })) });
const DAY = 24 * 3600 * 1000;

function mainFor(storage: ReturnType<typeof listStorage>, session: string, hub = memoryTransportHub(), now = () => 10 * DAY) {
  const bus = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(windowBusChannelName("/", "core", session)), now, timers: false });
  bus.start();
  const opened: string[] = [];
  const uninstall = installMainWindow(bus, {
    reveal: () => true,
    hide: () => true,
    isOpen: () => false,
    panels: () => [],
    storage,
    session,
    now,
    command: false,
    open: (id) => (opened.push(id), true),
    claimWaitMs: 20,
    channel: (s) => hub.connect(windowBusChannelName("/", "core", s)),
  });
  return { bus, opened, uninstall, hub };
}

test("storage keys are per session: layouts, labels and the dashboard record", () => {
  assert.match(windowLayoutKey("w2", "sessA123", "core"), /^ada:layout-window:core:sessA123:w2$/);
  assert.match(windowLabelKey("w2", "sessA123", "core"), /^ada:window-label:core:sessA123:w2$/);
  assert.match(dashboardKey("sessA123", "core"), /^ada:dashboard:core:sessA123$/);
  assert.notEqual(windowLayoutKey("w2", "sessA123"), windowLayoutKey("w2", "sessB456"));
  assert.notEqual(dashboardKey("sessA123"), dashboardKey("sessB456"));
});

test("two main windows of different sessions keep their dashboards apart in one storage", () => {
  const storage = listStorage();
  const hub = memoryTransportHub();
  const a = mainFor(storage, "sessA123", hub);
  const b = mainFor(storage, "sessB456", hub);
  const wa = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(windowBusChannelName("/", "core", "sessA123")), timers: false });
  wa.start();
  wa.setPanels(["demo:a"]);
  assert.match(storage.getItem(dashboardKey("sessA123")) ?? "", /"w2"/);
  assert.equal(storage.getItem(dashboardKey("sessB456")), null, "b never heard of it");
  assert.equal(b.bus.windows().length, 0);
  wa.stop();
  a.uninstall();
  b.uninstall();
});

test("a new session offers the last dashboard's windows and adopts them on reopen", async () => {
  const storage = listStorage();
  const old = "sessOLD1";
  storage.setItem(dashboardKey(old), rec("w2", "w3"));
  storage.setItem(lastDashboardKey(), JSON.stringify({ v: 1, session: old }));
  storage.setItem(windowLayoutKey("w2", old), '{"layout":"two"}');
  storage.setItem(windowLabelKey("w2", old), "Plots");
  storage.setItem(windowLayoutKey("w3", old), '{"layout":"three"}');
  const s = mainFor(storage, "sessNEW1");
  await dashboardProbed();
  const items = closedWindows();
  assert.deepEqual(items.map((w) => w.id), ["w2", "w3"], "offered");
  assert.equal(storage.getItem(windowLayoutKey("w2", "sessNEW1")), null, "nothing copied before the click");
  assert.deepEqual(reopenWindows(), ["w2", "w3"]);
  assert.deepEqual(s.opened, ["w2", "w3"]);
  assert.equal(storage.getItem(windowLayoutKey("w2", "sessNEW1")), '{"layout":"two"}', "layout copied");
  assert.equal(storage.getItem(windowLabelKey("w2", "sessNEW1")), "Plots", "label copied");
  assert.equal(storage.getItem(windowLayoutKey("w3", "sessNEW1")), '{"layout":"three"}');
  assert.match(storage.getItem(dashboardKey("sessNEW1")) ?? "", /"w3"/, "recorded in the new session");
  assert.deepEqual(JSON.parse(storage.getItem(lastDashboardKey())!), { v: 1, session: "sessNEW1" }, "the last dashboard is now this one");
  assert.equal(storage.getItem(windowLayoutKey("w2", old)), '{"layout":"two"}', "copied, not moved");
  s.uninstall();
});

test("the last dashboard is not offered while its main window is still live, or to its own session", async () => {
  const storage = listStorage();
  storage.setItem(dashboardKey("sessOLD1"), rec("w2"));
  storage.setItem(lastDashboardKey(), JSON.stringify({ v: 1, session: "sessOLD1" }));
  const hub = memoryTransportHub();
  const holder = mainFor(storage, "sessOLD1", hub); // the old session's own main: it reloaded, its own record is its own
  await dashboardProbed();
  assert.deepEqual(closedWindows().map((w) => w.id), ["w2"], "its own session: its record");
  holder.uninstall();
  // A second browser window with a new session, while the first is live.
  const first = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(windowBusChannelName("/", "core", "sessOLD1")), timers: false });
  first.start();
  const second = mainFor(storage, "sessNEW2", hub);
  await dashboardProbed();
  assert.deepEqual(closedWindows(), [], "the first window's dashboard is live: not adopted");
  second.uninstall();
});

test("sessions beyond the newest N, or unused for too long, are pruned with their keys; the current and last are kept", () => {
  const storage = listStorage();
  const now = 100 * DAY;
  const mk = (id: string, ageDays: number) => {
    touchSession(storage, id, now - ageDays * DAY, "core");
    storage.setItem(dashboardKey(id, "core"), rec("w2"));
    storage.setItem(windowLayoutKey("w2", id, "core"), "{}");
    storage.setItem(windowLabelKey("w2", id, "core"), "x");
  };
  for (let i = 0; i < 12; i++) mk(`recent${String(i).padStart(2, "0")}`, 1 + i * 0.1);
  mk("ancient1", 40);
  mk("lastSess", 30); // old, but the last dashboard's
  mk("currentS", 0);
  storage.setItem(windowLayoutKey("w9", "orphan01", "core"), "{}"); // never registered
  storage.setItem("unrelated", "keep");
  const dropped = pruneSessions(storage, { keep: ["currentS", "lastSess"], now, shellId: "core" });
  assert.ok(dropped.includes("ancient1"));
  assert.ok(dropped.includes("orphan01"), "unregistered keys go too");
  assert.equal(storage.getItem(windowLayoutKey("w9", "orphan01", "core")), null);
  assert.equal(storage.getItem(dashboardKey("ancient1", "core")), null);
  assert.equal(storage.getItem(windowLayoutKey("w2", "ancient1", "core")), null);
  assert.equal(storage.getItem(windowLabelKey("w2", "ancient1", "core")), null);
  const left = ["currentS", "lastSess", ...Array.from({ length: 12 }, (_, i) => `recent${String(i).padStart(2, "0")}`)].filter(
    (id) => storage.getItem(dashboardKey(id, "core")) !== null,
  );
  assert.equal(left.length, SESSION_KEEP, "the newest N in all, kept ones counted");
  assert.ok(left.includes("currentS") && left.includes("lastSess"));
  assert.ok(left.includes("recent00") && !left.includes("recent11"), "the oldest of the recent go first");
  assert.equal(storage.getItem("unrelated"), "keep");
  assert.ok(!(JSON.parse(storage.getItem(sessionsKey("core"))!).at as object).hasOwnProperty("ancient1"));
});

test("installing the main window prunes, keeping its own session and the last dashboard's", () => {
  const storage = listStorage();
  const now = () => 100 * DAY;
  for (let i = 0; i < 12; i++) {
    touchSession(storage, `old${String(i).padStart(5, "0")}`, now() - (i + 1) * 3600 * 1000, "core");
    storage.setItem(dashboardKey(`old${String(i).padStart(5, "0")}`, "core"), rec("w2"));
  }
  storage.setItem(lastDashboardKey("core"), JSON.stringify({ v: 1, session: "old00011" }));
  const s = mainFor(storage, "sessCUR1", memoryTransportHub(), now);
  assert.ok(storage.getItem(dashboardKey("old00011", "core")) !== null, "the last dashboard's survives");
  const sessions = Object.keys(JSON.parse(storage.getItem(sessionsKey("core"))!).at);
  assert.equal(sessions.length, SESSION_KEEP);
  assert.ok(sessions.includes("sessCUR1"));
  s.uninstall();
});
