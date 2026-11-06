// The window bus: transports, presence, the move handshake, and what comes back
// when a window closes.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAIN_WINDOW_ID,
  acceptPanels,
  broadcastTransport,
  closedDashboardWindows,
  createWindowBus,
  defaultWindowLabel,
  emptyDashboard,
  forgetWindow,
  isWindowBusMessage,
  isWindowId,
  mainWindowLive,
  memoryTransportHub,
  nextWindowId,
  nullTransport,
  panelsToReturn,
  readDashboard,
  recordWindow,
  sendPanel,
  windowBusChannelName,
  windowShowing,
  type WindowBus,
} from "@/layout/windowBus";

function clock(start = 1000) {
  const c = { t: start, now: () => c.t };
  return c;
}

function pair() {
  const hub = memoryTransportHub();
  const c = clock();
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), now: c.now, timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now: c.now, timers: false });
  return { hub, c, main, w2 };
}

test("the in-memory hub reaches every other member, never the sender", () => {
  const hub = memoryTransportHub();
  const a = hub.connect();
  const b = hub.connect();
  const c = hub.connect();
  const got: string[] = [];
  a.subscribe(() => got.push("a"));
  b.subscribe(() => got.push("b"));
  c.subscribe(() => got.push("c"));
  a.post({ type: "goodbye", from: "x", panels: [] });
  assert.deepEqual(got.sort(), ["b", "c"]);
  c.close();
  got.length = 0;
  a.post({ type: "goodbye", from: "x", panels: [] });
  assert.deepEqual(got, ["b"]);
});

test("without BroadcastChannel the transport is a no-op", () => {
  const g = globalThis as Record<string, unknown>;
  const had = g.BroadcastChannel;
  delete g.BroadcastChannel;
  try {
    const t = broadcastTransport("x");
    assert.equal(t, nullTransport);
    t.post({ type: "goodbye", from: "x", panels: [] });
    t.subscribe(() => assert.fail("nothing arrives"))();
  } finally {
    if (had) g.BroadcastChannel = had;
  }
});

test("the channel is scoped by the page path and the shell", () => {
  assert.notEqual(windowBusChannelName("/", "core"), windowBusChannelName("/", "alt"));
  assert.notEqual(windowBusChannelName("/a/", "core"), windowBusChannelName("/b/", "core"));
  assert.equal(windowBusChannelName("", "core"), windowBusChannelName("/", "core"));
});

test("messages are recognised by type and sender", () => {
  assert.equal(isWindowBusMessage({ type: "hello", from: "w2", label: "x", panels: [], main: false }), true);
  assert.equal(isWindowBusMessage({ type: "nope", from: "w2" }), false);
  assert.equal(isWindowBusMessage({ type: "hello" }), false);
  assert.equal(isWindowBusMessage(null), false);
});

test("presence: hello is answered, panels are announced, the main window is first", () => {
  const { main, w2, hub, c } = pair();
  main.start();
  main.setPanels(["core:tree"]);
  w2.start();
  assert.deepEqual(main.windows().map((w) => w.id), ["w2"]);
  assert.deepEqual(w2.windows().map((w) => w.id), ["main"], "the main window answered the hello");
  assert.equal(w2.windows()[0].main, true);
  assert.deepEqual(w2.windows()[0].panels, ["core:tree"]);
  w2.setPanels(["demo:a"]);
  assert.deepEqual(main.windows()[0].panels, ["demo:a"]);
  assert.equal(windowShowing(main, "demo:a")?.id, "w2");
  assert.equal(mainWindowLive(w2), true);
  const w3 = createWindowBus({ id: "w3", label: "Window 3", transport: hub.connect(), now: c.now, timers: false });
  w3.start();
  assert.deepEqual(w2.windows().map((w) => w.id), ["main", "w3"]);
  w2.setLabel("Charts");
  assert.equal(main.windows().find((w) => w.id === "w2")?.label, "Charts");
});

test("subscribers are told of changes; version moves", () => {
  const { main, w2 } = pair();
  main.start();
  let n = 0;
  main.subscribe(() => n++);
  const v = main.version();
  w2.start();
  assert.ok(n > 0);
  assert.notEqual(main.version(), v);
  const before = n;
  w2.setPanels([]); // unchanged
  assert.equal(n, before);
});

test("a silent window is dropped as lost; a heartbeat keeps it", () => {
  const { main, w2, c } = pair();
  const gone: string[] = [];
  main.onGone((w, why) => gone.push(`${w.id}:${why}`));
  main.start();
  w2.start();
  c.t += 10000;
  w2.tick();
  main.tick();
  assert.equal(main.windows().length, 1);
  c.t += 16000;
  main.tick();
  assert.deepEqual(gone, ["w2:lost"]);
  assert.equal(main.windows().length, 0);
});

test("goodbye: dropped as closed after the grace, unless it comes straight back (a reload)", () => {
  const { main, w2, hub, c } = pair();
  const gone: string[] = [];
  main.onGone((w, why) => gone.push(`${w.id}:${why}:${w.panels.join(",")}`));
  main.start();
  w2.start();
  w2.setPanels(["demo:a"]);
  w2.stop();
  assert.equal(main.windows().length, 0, "a leaving window is not listed");
  // The reloaded page says hello again within the grace.
  const again = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now: c.now, timers: false });
  again.start();
  c.t += 2000;
  main.tick();
  assert.deepEqual(gone, []);
  assert.equal(main.windows().length, 1);
  again.setPanels(["demo:a"]);
  again.stop();
  c.t += 2000;
  main.tick();
  assert.deepEqual(gone, ["w2:closed:demo:a"]);
});

test("the main window going away leaves a pop-out without it", () => {
  const { main, w2, c } = pair();
  main.start();
  w2.start();
  assert.equal(mainWindowLive(w2), true);
  main.stop();
  c.t += 2000;
  w2.tick();
  assert.equal(mainWindowLive(w2), false);
});

// ---------------------------------------------------------------------------
// The move handshake.
// ---------------------------------------------------------------------------

function timers() {
  const pending = new Map<number, () => void>();
  let n = 0;
  return {
    setTimer: (fn: () => void) => {
      pending.set(++n, fn);
      return n;
    },
    clearTimer: (h: unknown) => void pending.delete(h as number),
    fire() {
      for (const [k, fn] of [...pending]) {
        pending.delete(k);
        fn();
      }
    },
    get size() {
      return pending.size;
    },
  };
}

function holder(bus: WindowBus, initial: string[]) {
  const h = {
    panels: [...initial],
    take(id: string) {
      h.panels = h.panels.filter((p) => p !== id);
      bus.setPanels(h.panels);
    },
    put(id: string) {
      if (!h.panels.includes(id)) h.panels = [...h.panels, id];
      bus.setPanels(h.panels);
    },
  };
  bus.setPanels(h.panels);
  return h;
}

test("a moved panel leaves the source and docks in the target, which acknowledges", async () => {
  const { main, w2 } = pair();
  main.start();
  w2.start();
  const src = holder(main, ["core:admin", "core:tree"]);
  const dst = holder(w2, []);
  acceptPanels(w2, (id) => (dst.put(id), true));
  const t = timers();
  const ok = await sendPanel(main, "core:admin", "w2", { remove: () => src.take("core:admin"), restore: () => src.put("core:admin"), ...t });
  assert.equal(ok, true);
  assert.deepEqual(src.panels, ["core:tree"]);
  assert.deepEqual(dst.panels, ["core:admin"]);
  assert.equal(t.size, 0, "the timeout is cleared");
  assert.deepEqual(main.windows()[0].panels, ["core:admin"]);
});

test("no acknowledgement in time: the panel goes back", async () => {
  const { main, w2 } = pair();
  main.start();
  w2.start();
  const src = holder(main, ["core:admin"]);
  const t = timers();
  const p = sendPanel(main, "core:admin", "w9", { remove: () => src.take("core:admin"), restore: () => src.put("core:admin"), ...t });
  assert.deepEqual(src.panels, []);
  t.fire();
  assert.equal(await p, false);
  assert.deepEqual(src.panels, ["core:admin"]);
});

test("a refusal puts the panel back at once; another window's ack is not ours", async () => {
  const { main, w2, hub, c } = pair();
  const w3 = createWindowBus({ id: "w3", label: "Window 3", transport: hub.connect(), now: c.now, timers: false });
  main.start();
  w2.start();
  w3.start();
  acceptPanels(w2, () => false);
  let dockedIn3 = 0;
  acceptPanels(w3, () => (dockedIn3++, true));
  const src = holder(main, ["a"]);
  const t = timers();
  assert.equal(await sendPanel(main, "a", "w2", { remove: () => src.take("a"), restore: () => src.put("a"), ...t }), false);
  assert.deepEqual(src.panels, ["a"]);
  assert.equal(dockedIn3, 0, "only the addressed window docks it");
  // A dock that throws is a refusal too.
  acceptPanels(w3, () => {
    throw new Error("boom");
  });
});

// ---------------------------------------------------------------------------
// Closing a window, ids, the dashboard.
// ---------------------------------------------------------------------------

test("a closed window's panels come back unless shown elsewhere or already open", () => {
  assert.deepEqual(
    panelsToReturn(["a", "b", "c", "a"], [{ panels: ["b"] }], (id) => id === "c"),
    ["a"],
  );
});

test("window ids: the least free number, labels from it", () => {
  assert.deepEqual(nextWindowId([]), { id: "w2", label: "Window 2" });
  assert.deepEqual(nextWindowId(["main", "w2", "w4"]), { id: "w3", label: "Window 3" });
  assert.equal(defaultWindowLabel("main"), "Main");
  assert.equal(defaultWindowLabel("w7"), "Window 7");
  assert.equal(isWindowId("w2"), true);
  assert.equal(isWindowId("main"), false);
  assert.equal(isWindowId("a/b"), false);
  assert.equal(isWindowId(""), false);
});

test("the dashboard record: upsert, forget, read back safely, the closed ones", () => {
  let rec = emptyDashboard();
  rec = recordWindow(rec, { id: "w2", label: "Window 2", panels: ["a"] });
  rec = recordWindow(rec, { id: "w3", label: "Window 3", panels: ["b"] });
  const same = recordWindow(rec, { id: "w2", label: "Window 2", panels: ["a"] });
  assert.equal(same, rec, "no change, same record");
  rec = recordWindow(rec, { id: "w2", label: "Charts", panels: ["a", "c"] });
  assert.deepEqual(rec.windows[0], { id: "w2", label: "Charts", panels: ["a", "c"] });
  assert.deepEqual(closedDashboardWindows(rec, [{ id: "w3" }]).map((w) => w.id), ["w2"]);
  rec = forgetWindow(rec, "w3");
  assert.deepEqual(rec.windows.map((w) => w.id), ["w2"]);
  const back = readDashboard(JSON.parse(JSON.stringify(rec)));
  assert.deepEqual(back, rec);
  assert.deepEqual(readDashboard({ v: 1, windows: [{ id: "../x" }, { id: "w5", panels: [1, "p"] }, { id: "w5" }] }), {
    v: 1,
    windows: [{ id: "w5", label: "Window 5", panels: ["p"] }],
  });
  assert.deepEqual(readDashboard("junk"), emptyDashboard());
});
