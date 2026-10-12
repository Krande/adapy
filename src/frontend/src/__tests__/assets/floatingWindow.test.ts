import assert from "node:assert/strict";
import test from "node:test";

import { clampRect, defaultRect, parseStoredRect } from "../../assets/floatingWindow";

const MIN = { width: 420, height: 320 };
const VIEW = { width: 1600, height: 900 };

test("a rect inside the viewport is kept as it is", () => {
  const r = { x: 100, y: 50, width: 800, height: 500 };
  assert.deepEqual(clampRect(r, VIEW, MIN), r);
});

test("a rect off screen is moved back, its size kept", () => {
  // saved on a wider screen: off the right and bottom edges
  assert.deepEqual(clampRect({ x: 2500, y: 1200, width: 800, height: 500 }, VIEW, MIN), {
    x: 800,
    y: 400,
    width: 800,
    height: 500,
  });
  // dragged past the top-left: the title bar comes back on screen
  assert.deepEqual(clampRect({ x: -300, y: -40, width: 800, height: 500 }, VIEW, MIN), {
    x: 0,
    y: 0,
    width: 800,
    height: 500,
  });
});

test("the size is bounded by the minimum and by the viewport", () => {
  assert.deepEqual(clampRect({ x: 10, y: 10, width: 100, height: 50 }, VIEW, MIN), {
    x: 10,
    y: 10,
    width: 420,
    height: 320,
  });
  assert.deepEqual(clampRect({ x: 10, y: 10, width: 5000, height: 5000 }, VIEW, MIN), {
    x: 0,
    y: 0,
    width: 1600,
    height: 900,
  });
  // a viewport smaller than the minimum gets the viewport, not an overflowing window
  assert.deepEqual(clampRect({ x: 50, y: 50, width: 800, height: 600 }, { width: 400, height: 300 }, MIN), {
    x: 0,
    y: 0,
    width: 400,
    height: 300,
  });
});

test("the first open is centred near the top, within the viewport", () => {
  assert.deepEqual(defaultRect(VIEW, { width: 1000, height: 640 }, MIN), { x: 300, y: 60, width: 1000, height: 640 });
  const small = defaultRect({ width: 800, height: 600 }, { width: 1000, height: 640 }, MIN);
  assert.deepEqual(small, { x: 16, y: 60, width: 768, height: 520 });
});

test("a stored rect is read back defensively", () => {
  assert.deepEqual(parseStoredRect(JSON.stringify({ x: 1, y: 2, width: 500, height: 400, extra: true })), {
    x: 1,
    y: 2,
    width: 500,
    height: 400,
  });
  for (const raw of [
    null,
    "",
    "{nope",
    "[]",
    "3",
    JSON.stringify({ x: 1, y: 2, width: 500 }),
    JSON.stringify({ x: "1", y: 2, width: 500, height: 400 }),
    JSON.stringify({ x: 1, y: 2, width: 0, height: 400 }),
    JSON.stringify({ x: 1, y: null, width: 500, height: 400 }),
  ]) {
    assert.equal(parseStoredRect(raw), null, String(raw));
  }
});
