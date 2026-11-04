import assert from "node:assert/strict";
import test from "node:test";

import { centredRect, clampRect, defaultRect, moveRect, parseStoredRect, resizeRect } from "../../assets/floatingWindow";

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

// The Simulation panel's floating dialog: its first rect and its pointer drag, which it
// computed inline before the geometry had one home.

test("centredRect centres a preferred size both ways, inside the margins", () => {
  const r = centredRect({ width: 1440, height: 900 }, { width: 560, height: 640 }, { x: 24, y: 48, top: 48 });
  assert.deepEqual(r, { x: 440, y: 130, width: 560, height: 640 });
});

test("centredRect shrinks to the viewport less its margins, and keeps the top margin", () => {
  const r = centredRect({ width: 500, height: 600 }, { width: 560, height: 640 }, { x: 24, y: 48, top: 48 });
  // width min(560, 500-48) = 452, x max(24, 24) ; height min(640, 600-96) = 504, y max(48, 48)
  assert.deepEqual(r, { x: 24, y: 48, width: 452, height: 504 });
});

test("moveRect follows the pointer but not past the top-left corner", () => {
  const start = { x: 100, y: 80, width: 300, height: 200 };
  assert.deepEqual(moveRect(start, 25, -10), { x: 125, y: 70, width: 300, height: 200 });
  assert.deepEqual(moveRect(start, -500, -500), { x: 0, y: 0, width: 300, height: 200 });
});

test("resizeRect grows from the corner and stops at the minimum", () => {
  const start = { x: 100, y: 80, width: 300, height: 200 };
  const min = { width: 280, height: 200 };
  assert.deepEqual(resizeRect(start, 50, 40, min), { x: 100, y: 80, width: 350, height: 240 });
  assert.deepEqual(resizeRect(start, -100, -100, min), { x: 100, y: 80, width: 280, height: 200 });
});