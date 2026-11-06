// Mirroring the main window's state to pop-out windows: snapshots on change and
// on request, applied in order, commands run in the main window; and core's
// selection topic over the object info store.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { createWindowBus, memoryTransportHub, MAIN_WINDOW_ID } = await import("@/layout/windowBus");
const { followMirror, hostMirror, registerMirrorCommand, registerMirrorTopic, resetMirror, sendMirrorCommand } = await import("@/layout/windowMirror");

function counterStore(initial = 0) {
  const s = {
    value: initial,
    cbs: new Set<() => void>(),
    set(v: number) {
      s.value = v;
      for (const cb of s.cbs) cb();
    },
    subscribe(cb: () => void) {
      s.cbs.add(cb);
      return () => void s.cbs.delete(cb);
    },
  };
  return s;
}

function setup() {
  resetMirror();
  const hub = memoryTransportHub();
  const c = { t: 1000 };
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), now: () => c.t, timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now: () => c.t, timers: false });
  // Main's store, and the pop-out's copy (one module, two pages: two variables here).
  const mainState = counterStore(1);
  const copy = { value: -1, applied: 0 };
  registerMirrorTopic<{ n: number }>({
    id: "t:counter",
    snapshot: () => ({ n: mainState.value }),
    subscribe: (cb) => mainState.subscribe(cb),
    apply: (d) => {
      copy.value = d.n;
      copy.applied++;
    },
  });
  const ran: string[] = [];
  registerMirrorCommand<{ by: number }>({ id: "t:add", run: (a, from) => (ran.push(from), mainState.set(mainState.value + a.by)) });
  const queue: Array<() => void> = [];
  const flush = () => {
    while (queue.length) queue.shift()!();
  };
  return { hub, c, main, w2, mainState, copy, ran, schedule: (fn: () => void) => void queue.push(fn), flush };
}

test("a pop-out asks for the snapshots when it starts and applies them", () => {
  const s = setup();
  s.main.start();
  hostMirror(s.main, s.schedule);
  s.w2.start();
  followMirror(s.w2);
  assert.equal(s.copy.value, 1);
});

test("changes are published, coalesced to one message per tick; a command runs in the main window and comes back", () => {
  const s = setup();
  s.main.start();
  hostMirror(s.main, s.schedule);
  s.w2.start();
  followMirror(s.w2);
  const before = s.copy.applied;
  s.mainState.set(2);
  s.mainState.set(3);
  assert.equal(s.copy.value, 1, "not before the tick");
  s.flush();
  assert.equal(s.copy.value, 3);
  assert.equal(s.copy.applied, before + 1, "one message for both changes");
  sendMirrorCommand(s.w2, "t:add", { by: 10 });
  s.flush();
  assert.deepEqual(s.ran, ["w2"]);
  assert.equal(s.copy.value, 13);
  // An unknown command is warned about, not thrown.
  sendMirrorCommand(s.w2, "t:nope");
});

test("nothing is published while no other window listens", () => {
  const s = setup();
  s.main.start();
  hostMirror(s.main, s.schedule);
  let posted = 0;
  s.main.on("mirror", () => posted++);
  s.mainState.set(5);
  s.flush();
  assert.equal(posted, 0);
});

test("an older snapshot than the one applied is skipped; a new main window starts over", () => {
  const s = setup();
  s.w2.start();
  followMirror(s.w2);
  const fake = s.hub.connect();
  fake.post({ type: "hello", from: MAIN_WINDOW_ID, label: "Main", panels: [], main: true });
  fake.post({ type: "mirror", from: MAIN_WINDOW_ID, topic: "t:counter", data: { n: 7 }, seq: 5 });
  fake.post({ type: "mirror", from: MAIN_WINDOW_ID, topic: "t:counter", data: { n: 6 }, seq: 4 });
  assert.equal(s.copy.value, 7);
  // The main window goes and a new one comes: its snapshots number from 1.
  let requested = 0;
  fake.subscribe((m) => void (m.type === "mirror-request" && requested++));
  fake.post({ type: "goodbye", from: MAIN_WINDOW_ID, panels: [] });
  s.c.t += 2000;
  s.w2.tick();
  fake.post({ type: "hello", from: MAIN_WINDOW_ID, label: "Main", panels: [], main: true });
  assert.equal(requested, 1, "asked again");
  fake.post({ type: "mirror", from: MAIN_WINDOW_ID, topic: "t:counter", data: { n: 2 }, seq: 1 });
  assert.equal(s.copy.value, 2);
});

test("a topic with a delta sends it on a change and the snapshot on a request; a refused apply asks again", () => {
  const s = setup();
  let n = 1;
  const store = counterStore(0);
  const applied: unknown[] = [];
  registerMirrorTopic<{ full?: number; d?: number }>({
    id: "t:delta",
    snapshot: () => ({ full: store.value }),
    delta: () => (store.value === 99 ? null : { d: n++ }),
    subscribe: (cb) => store.subscribe(cb),
    apply: (x) => {
      applied.push(x);
      return x.d !== 2;
    },
  });
  s.main.start();
  hostMirror(s.main, s.schedule);
  s.w2.start();
  followMirror(s.w2);
  assert.deepEqual(applied, [{ full: 0 }]);
  store.set(1);
  s.flush();
  assert.deepEqual(applied.at(-1), { d: 1 });
  // Refused: the window asks for the topic, and gets the snapshot.
  store.set(2);
  s.flush();
  assert.deepEqual(applied.slice(-2), [{ d: 2 }, { full: 2 }]);
  // A null delta: nothing sent.
  const len = applied.length;
  store.set(99);
  s.flush();
  assert.equal(applied.length, len);
});

test("core's selection topic carries the object info store's data, not its panel flag", async () => {
  resetMirror();
  const { registerCoreMirror, resetCoreMirror, selectionSnapshot, findTreeNode, SELECTION_TOPIC } = await import("@/layout/windowMirrorCore");
  const { resetSceneActionCommands } = await import("@/layout/sceneActions");
  resetCoreMirror();
  resetSceneActionCommands();
  const { listMirrorTopics } = await import("@/layout/windowMirror");
  const { useObjectInfoStore } = await import("@/state/objectInfoStore");
  registerCoreMirror();
  const topic = listMirrorTopics().find((t) => t.id === SELECTION_TOPIC)!;
  assert.ok(topic);
  useObjectInfoStore.setState({ name: "Beam 1", selectedNodeId: "n7", jsonData: { a: 1 }, show_info_box: true });
  const snap = selectionSnapshot();
  assert.deepEqual(snap, {
    name: "Beam 1",
    selectedNodeId: "n7",
    faceIndex: null,
    clickCoordinate: null,
    clickedFace: null,
    jsonData: { a: 1 },
    fileName: null,
    rangeCount: 0,
    parentName: null,
  });
  assert.equal("show_info_box" in snap, false);
  let n = 0;
  const off = topic.subscribe(() => n++);
  useObjectInfoStore.getState().toggle();
  assert.equal(n, 0, "the panel flag is each window's own");
  useObjectInfoStore.getState().setName("Beam 2");
  assert.equal(n, 1);
  off();
  // Applied in a pop-out: the data, and the flag left alone.
  useObjectInfoStore.setState({ show_info_box: false });
  topic.apply({ ...snap, name: "Plate", rangeCount: 3, parentName: "Deck" });
  assert.equal(useObjectInfoStore.getState().name, "Plate");
  assert.equal(useObjectInfoStore.getState().show_info_box, false);
  // What the panel derives from the 3D selection goes to the mirror store, not the object info store.
  const { useSceneMirrorStore } = await import("@/state/sceneMirrorStore");
  assert.equal(useSceneMirrorStore.getState().rangeCount, 3);
  assert.equal(useSceneMirrorStore.getState().parentName, "Deck");
  assert.equal("rangeCount" in useObjectInfoStore.getState(), false);
  const tree = { id: "r", name: "root", children: [{ id: "a", name: "A", children: [{ id: "n7", name: "Beam", children: [] }] }] };
  assert.equal(findTreeNode(tree as never, "n7")?.name, "Beam");
  assert.equal(findTreeNode(tree as never, "zz"), null);
  assert.equal(findTreeNode(null, "n7"), null);
});
