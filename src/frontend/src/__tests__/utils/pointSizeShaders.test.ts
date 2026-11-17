// A point drawn at a world size and the same point in the GPU point picker are sized alike, from the
// projection: over the depth in perspective (w = -z), over 1 in an orthographic view. The orthographic
// view draws what is behind its eye too (near = -far); sized from the distance to the eye, such a point
// became screen-sized in the picker and every click picked it. (The shaders run on the GPU only, so
// their sources are read here.)

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const SIZE = "uWorldPointSize * projectionMatrix[1][1] * 0.5 * uViewportHeight / max(gl_Position.w, 0.1)";

for (const [what, path] of [
  ["the drawn point", "../../utils/scene/pointsImpostor.ts"],
  ["the picker's point", "../../utils/mesh_select/GpuPointPicker.ts"],
] as const) {
  test(`${what} is sized from the projection, capped, never from the distance to the eye`, () => {
    const s = source(path);
    assert.ok(s.includes(SIZE), "pixels per world unit over w");
    assert.match(s, /pixelSize = min\(pixelSize, 1000\.0\)/);
    assert.doesNotMatch(s, /-mvPosition\.z/);
  });
}

test("the formula: the perspective size it replaced, and a fixed size at any depth in an orthographic view", () => {
  const pixels = (p11: number, height: number, w: number, d: number) => Math.min((d * p11 * 0.5 * height) / Math.max(w, 0.1), 1000);
  const fov = 50, height = 800, d = 0.3, depth = 12;
  const before = (d * (height / (2 * Math.tan(((fov * Math.PI) / 180) * 0.5)))) / depth;
  assert.ok(Math.abs(pixels(1 / Math.tan(((fov * Math.PI) / 180) * 0.5), height, depth, d) - before) < 1e-9);
  const halfHeight = 20;
  assert.equal(pixels(1 / halfHeight, height, 1, d), (d / (2 * halfHeight)) * height, "an orthographic view: w is 1");
});
