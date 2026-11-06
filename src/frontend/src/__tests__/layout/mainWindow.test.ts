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
