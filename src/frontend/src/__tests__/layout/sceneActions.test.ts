// The scene actions' seam: from a pop-out window each action is a mirror command
// the main window runs with its own scene actions; core-command actions go by
// `core:run-command` and only for the allowed ids; row commands carry ids, which
// the main window resolves to its own tree rows.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { createWindowBus, memoryTransportHub, MAIN_WINDOW_ID } = await import("@/layout/windowBus");
const { hostMirror, resetMirror, sendMirrorCommand } = await import("@/layout/windowMirror");
const { registerCoreMirror, resetCoreMirror } = await import("@/layout/windowMirrorCore");
const { localSceneActions, remoteSceneActions, resetSceneActionCommands, isMirroredWindow, SCENE_ACTION_COMMANDS, argIds } = await import("@/layout/sceneActions");
const { CORE_COMMAND_IDS } = await import("@/layout/coreCommandIds");
const { useTreeViewStore } = await import("@/state/treeViewStore");

type Call = [string, ...unknown[]];

/** Main and a pop-out on one in-memory hub, core's mirror hosted in main, its scene actions replaced by recorders. */
function setup() {
  resetMirror();
  resetCoreMirror();
  resetSceneActionCommands();
  const hub = memoryTransportHub();
  const c = { t: 1000 };
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), now: () => c.t, timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now: () => c.t, timers: false });
  main.start();
  w2.start();
  registerCoreMirror();
  hostMirror(main, () => {});
  const calls: Call[] = [];
  const a = localSceneActions as unknown as Record<string, unknown>;
  const saved = { ...a };
  for (const k of Object.keys(saved)) {
    a[k] = (...args: unknown[]) => {
      calls.push([k, ...args]);
      return Promise.resolve();
    };
  }
  const restore = () => Object.assign(a, saved);
  return { main, w2, calls, restore, remote: remoteSceneActions(w2) };
}

test("this page (no ?window=) is the main window", () => {
  assert.equal(isMirroredWindow(), false);
});

test("the object info's buttons from a pop-out run in the main window", () => {
  const s = setup();
  try {
    s.remote.hideSelection();
    s.remote.hideOthers();
    s.remote.showAll();
    s.remote.fitAll();
    s.remote.fitSelection();
    s.remote.clearSelection();
    s.remote.undo();
    s.remote.redo();
    s.remote.selectParent();
    s.remote.selectNode("n7");
    assert.deepEqual(s.calls, [
      ["hideSelection"],
      ["hideOthers"],
      ["showAll"],
      ["fitAll"],
      ["fitSelection"],
      ["clearSelection"],
      ["undo"],
      ["redo"],
      ["selectParent"],
      ["selectNode", "n7"],
    ]);
    // Only the listed core commands: anything else asked for is ignored.
    s.calls.length = 0;
    sendMirrorCommand(s.w2, SCENE_ACTION_COMMANDS.runCommand, { id: CORE_COMMAND_IDS.resetLayout });
    sendMirrorCommand(s.w2, SCENE_ACTION_COMMANDS.runCommand, { id: 42 });
    sendMirrorCommand(s.w2, SCENE_ACTION_COMMANDS.selectNode, { id: { x: 1 } });
    assert.deepEqual(s.calls, []);
  } finally {
    s.restore();
  }
});

test("row commands carry ids; the main window acts on its own rows, unknown ids dropped", () => {
  const s = setup();
  try {
    const tree = {
      id: "__roots__",
      name: "",
      children: [{ id: "m1", name: "Model", children: [{ id: "a", name: "A", children: [] }, { id: "b", name: "B", children: [] }] }],
    };
    useTreeViewStore.getState().setTreeData(tree as never);
    const row = (id: string) => ({ id, name: id, children: [] });
    s.remote.hideRows([row("a"), row("zz")]);
    s.remote.showRows([row("b")]);
    s.remote.isolateRows([row("m1")]);
    s.remote.fitRows([row("a")]);
    s.remote.selectRows([row("a"), row("b")], "toggle");
    s.remote.selectRows([], "replace");
    // Nothing known: no action.
    s.remote.hideRows([row("zz")]);
    const ids = (c: Call) => [c[0], (c[1] as { id: string }[]).map((r) => r.id), ...c.slice(2)];
    assert.deepEqual(s.calls.map(ids), [
      ["hideRows", ["a"]],
      ["showRows", ["b"]],
      ["isolateRows", ["m1"]],
      ["fitRows", ["a"]],
      ["selectRowsFromElsewhere", ["a", "b"], "toggle"],
      ["selectRowsFromElsewhere", [], "replace"],
    ]);
    // The rows are the main window's own objects (its tree), not copies of the ids.
    assert.equal((s.calls[0][1] as unknown[])[0], tree.children[0].children[0]);
    useTreeViewStore.getState().clearTreeData();
  } finally {
    s.restore();
  }
});

test("argIds keeps strings only", () => {
  assert.deepEqual(argIds({ ids: ["a", 1, null, "b"] }), ["a", "b"]);
  assert.deepEqual(argIds(null), []);
  assert.deepEqual(argIds({ ids: "a" }), []);
  assert.deepEqual(argIds({ ids: ["a", "b", "c"] }, 2), ["a", "b"]);
});
