import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SWEEP_FRAMES,
  TIME_HISTORY_EXPORT_FPS,
  evenSize,
  exportFileName,
  planExportFrames,
} from "../../utils/scene/fea/animationExport/framePlan";

const labels = ["0", "0.0015", "0.003"];

test("a time history exports every step at true scale", () => {
  const plan = planExportFrames({
    timeHistory: true, nSteps: 3, stepIndex: 2, range: [0, 1], period: 2, stepLabels: labels,
  });
  assert.equal(plan.stepsChange, true);
  assert.equal(plan.fps, TIME_HISTORY_EXPORT_FPS);
  assert.deepEqual(plan.frames.map((f) => f.stepIndex), [0, 1, 2]);
  assert.deepEqual(plan.frames.map((f) => f.factor), [1, 1, 1]);
  assert.deepEqual(plan.frames.map((f) => f.label), labels);
});

test("a static step exports one sweep cycle at the current step", () => {
  const plan = planExportFrames({
    timeHistory: false, nSteps: 3, stepIndex: 1, range: [0, 1], period: 2, stepLabels: labels,
  });
  assert.equal(plan.stepsChange, false);
  assert.equal(plan.frames.length, SWEEP_FRAMES);
  assert.equal(plan.fps, SWEEP_FRAMES / 2);
  assert.ok(plan.frames.every((f) => f.stepIndex === 1 && f.label === "0.0015"));
  const factors = plan.frames.map((f) => f.factor);
  assert.ok(Math.min(...factors) >= 0 && Math.max(...factors) <= 1);
  assert.ok(Math.abs(factors[0] - 0.5) < 1e-12); // sin sweep starts mid-range
});

test("an eigen mode sweeps the signed range", () => {
  const plan = planExportFrames({
    timeHistory: false, nSteps: 5, stepIndex: 0, range: [-1, 1], period: 1, stepLabels: [],
  });
  const factors = plan.frames.map((f) => f.factor);
  assert.ok(Math.min(...factors) < -0.99 && Math.max(...factors) > 0.99);
});

test("video sizes are even and file names are safe", () => {
  assert.deepEqual(evenSize(1281, 721.6), [1280, 720]);
  assert.equal(exportFileName("fem/opencourant/cell_impact.radanim", "Von Mises", "mp4"), "cell_impact_Von_Mises.mp4");
  assert.equal(exportFileName(null, null, "gif"), "animation.gif");
});

import {
  DEFAULT_EXPORT_SETTINGS,
  normaliseExportSettings,
  resolveExportSize,
} from "../../utils/scene/fea/animationExport/framePlan";

test("resolution is the short side; aspect sets the long one", () => {
  assert.deepEqual(resolveExportSize({ resolution: 1080, aspect: "16:9" }, 800, 600), [1920, 1080]);
  assert.deepEqual(resolveExportSize({ resolution: 1080, aspect: "9:16" }, 800, 600), [1080, 1920]);
  assert.deepEqual(resolveExportSize({ resolution: 720, aspect: "1:1" }, 800, 600), [720, 720]);
  // "view" follows the viewport: a 4:3 view at 720p.
  assert.deepEqual(resolveExportSize({ resolution: 720, aspect: "view" }, 800, 600), [960, 720]);
  // a phone in portrait keeps its portrait shape
  assert.deepEqual(resolveExportSize({ resolution: 1080, aspect: "view" }, 390, 844), [1080, 2336]);
});

test("a size past the GPU limit shrinks both sides together, staying even", () => {
  const [w, h] = resolveExportSize({ resolution: 2160, aspect: "16:9" }, 1, 1, 2048);
  assert.equal(w, 2048);
  assert.equal(h % 2, 0);
  assert.ok(Math.abs(w / h - 16 / 9) < 0.01);
});

test("stored settings are coerced onto valid presets", () => {
  assert.deepEqual(normaliseExportSettings(null), DEFAULT_EXPORT_SETTINGS);
  assert.equal(normaliseExportSettings({ format: "gif", resolution: 1080 }).resolution, 1080);
  assert.equal(normaliseExportSettings({ format: "gif", resolution: 2160 }).resolution, 1080); // nearest GIF preset
  assert.equal(normaliseExportSettings({ fps: 7 }).fps, 12);
  assert.equal(normaliseExportSettings({ gizmo: false }).gizmo, false);
  assert.equal(normaliseExportSettings({}).gizmo, true); // gizmo is on by default
});
