/**
 * The Scene panel's Connection tab (`ada dev connections`): offered only on that viewer, placed with
 * the two other tabs that describe a joint, and reachable through the store's mode like every tab.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { activeSceneTab, sceneTabs } from "../../components/info_box_scene/sceneTabs";

test("the Connection tab is offered only where a connection library is served", () => {
  assert.ok(!sceneTabs({}, {}).some((t) => t.id === "connection"));
  assert.ok(sceneTabs({}, { connection: true }).some((t) => t.id === "connection"));
});

test("it sits right before Clashes, the joint's own clash", () => {
  const ids = sceneTabs({}, { connection: true, joints: true }).map((t) => t.id);
  assert.deepEqual(ids.slice(ids.indexOf("connection"), ids.indexOf("connection") + 3), [
    "connection",
    "clashes",
    "joints",
  ]);
});

test("the store's connection mode opens it, and falls back to Model where it is not offered", () => {
  assert.equal(activeSceneTab("connection", sceneTabs({}, { connection: true })), "connection");
  assert.equal(activeSceneTab("connection", sceneTabs({}, {})), "model");
});
