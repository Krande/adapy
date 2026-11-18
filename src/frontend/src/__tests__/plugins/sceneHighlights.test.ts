// `ctx.scene.highlightObjects` (plugin API 1.29.0): a plugin drawing objects in a colour of its own,
// through whichever viewport is on screen, without touching the selection or the undo history.
import assert from "node:assert/strict";
import { test } from "node:test";

import { endSceneHighlights, highlightObjects } from "@/plugins/sceneHighlights";
import type { ObjectMarks } from "@/viewport/api/objectColors";
import { setActiveViewport, type ViewportApi } from "@/viewport/api/viewportApi";

/** A viewport that only keeps what it was last asked to mark, as `model/id=#colour` text. */
function fakeViewport() {
  let last: string[] = [];
  let calls = 0;
  const api = {
    setObjectMarks: (marks: ObjectMarks) => {
      calls++;
      last = [];
      for (const [model, ids] of marks) for (const [id, color] of ids) last.push(`${model}/${id}=${color}`);
      last.sort();
    },
  } as unknown as ViewportApi;
  return { api, marked: () => last, calls: () => calls };
}

test("a highlight is drawn in its colour until it is removed, and set replaces its objects", () => {
  const view = fakeViewport();
  const withdraw = setActiveViewport(view.api);
  try {
    const h = highlightObjects("p", [{ model: "m1", id: "1" }], { color: "#ff8800" });
    assert.deepEqual(view.marked(), ["m1/1=#ff8800"]);
    h.set([{ model: "m1", id: "2" }, { model: "m2", id: "1" }]);
    assert.deepEqual(view.marked(), ["m1/2=#ff8800", "m2/1=#ff8800"]);
    h.set([]);
    assert.deepEqual(view.marked(), []);
    h.remove();
    h.remove(); // twice is once
    h.set([{ model: "m1", id: "1" }]); // after remove: nothing
    assert.deepEqual(view.marked(), []);
  } finally {
    withdraw();
  }
});

test("two highlights draw together; on one object the newer one's colour shows; a colour as [r, g, b]", () => {
  const view = fakeViewport();
  const withdraw = setActiveViewport(view.api);
  try {
    const a = highlightObjects("p", [{ model: "m", id: "1" }, { model: "m", id: "2" }], { color: [0, 0.5, 1] });
    const b = highlightObjects("p", [{ model: "m", id: "2" }], { color: "#00ff00" });
    assert.deepEqual(view.marked(), ["m/1=#0080ff", "m/2=#00ff00"]);
    b.remove();
    assert.deepEqual(view.marked(), ["m/1=#0080ff", "m/2=#0080ff"]);
    a.remove();
    assert.deepEqual(view.marked(), []);
  } finally {
    withdraw();
  }
});

test("removing an owner from the scene takes its highlights away, not another owner's", () => {
  const view = fakeViewport();
  const withdraw = setActiveViewport(view.api);
  try {
    highlightObjects("gone", [{ model: "m", id: "1" }], { color: "#ff0000" });
    const kept = highlightObjects("kept", [{ model: "m", id: "2" }], { color: "#0000ff" });
    endSceneHighlights("gone");
    assert.deepEqual(view.marked(), ["m/2=#0000ff"]);
    kept.remove();
    assert.deepEqual(view.marked(), []);
  } finally {
    withdraw();
  }
});

test("a viewport that comes on screen while a highlight is drawn draws it too; a viewport without marks is skipped", () => {
  const first = fakeViewport();
  const withdrawFirst = setActiveViewport(first.api);
  const h = highlightObjects("p", [{ model: "m", id: "1" }], { color: "#123456" });
  withdrawFirst();
  // A viewport that cannot mark: nothing throws.
  const withdrawPlain = setActiveViewport({} as ViewportApi);
  withdrawPlain();
  const second = fakeViewport();
  const withdrawSecond = setActiveViewport(second.api);
  assert.deepEqual(second.marked(), ["m/1=#123456"]);
  h.remove();
  assert.deepEqual(second.marked(), []);
  const calls = second.calls();
  withdrawSecond();
  // With no highlight left, a change of viewport asks nothing.
  setActiveViewport(second.api)();
  assert.equal(second.calls(), calls);
});
