// The connection builder's annotations (member axes, their labels, the weld labels) are drawn through
// the viewport on screen, not added to a three.js scene: a joint built from the Inspect tab on the
// three.js-free viewport showed none of them, while a plugin's lines, drawn through the viewport's
// layers, showed. Here a viewport that records what it is asked to draw stands in for either one.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { DevBuildResult } from "@/services/api/devConnections";
import type { Billboard } from "@/viewport/api/billboards";
import type { OverlayLines } from "@/viewport/api/overlayLayer";
import type { ViewportApi } from "@/viewport/api/viewportApi";

const { clearConnectionOverlay, renderConnectionOverlay } = await import("@/utils/scene/connectionOverlay");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

const PREVIEW = "connection-preview:box_to_box#1";
const ORIGIN: [number, number, number] = [10, 20, 30];

function recordingViewport(onScreen: Set<string>) {
  const lines: OverlayLines[] = [];
  const labels: Billboard[] = [];
  let disposed = 0;
  const viewport = {
    kind: "native",
    sourceOrigin: (name: string) => (onScreen.has(name) ? ORIGIN : null),
    overlays: () => ({
      addLines: (l: OverlayLines) => lines.push(l),
      dispose: () => {
        disposed++;
        lines.length = 0;
      },
    }),
    billboards: () => ({
      add: (b: Billboard) => labels.push(b),
      update: () => {},
      dispose: () => {
        disposed++;
        labels.length = 0;
      },
    }),
  } as unknown as ViewportApi;
  return { viewport, lines, labels, disposed: () => disposed };
}

const result = {
  properties: {
    members: [
      { name: "beam1", role: "incoming", type: "BEAM", section: { name: "IPE300" }, axis: [[0, 0, 0], [1, 0, 0]] },
      { name: "pl1", role: null, type: "PLATE", thickness: 0.01, centre: [0, 1, 0] },
    ],
    welds: [{ name: "w1", weld_type: "fillet", throat: 0.005, at: [0, 0, 1], joins_its_members: true }],
  },
} as unknown as DevBuildResult;

const ALL = { axes: true, welds: true, weldSymbols: false, weldDetails: false };

test("the axes and labels are drawn through the viewport on screen, placed by the preview's origin", () => {
  const rec = recordingViewport(new Set([PREVIEW]));
  const withdraw = setActiveViewport(rec.viewport);
  try {
    assert.equal(renderConnectionOverlay(PREVIEW, result, ALL), 3);
    // One axis: its model coordinates, moved by the source's origin.
    assert.equal(rec.lines.length, 1);
    assert.deepEqual([...rec.lines[0].segments], [0, 0, 0, 1, 0, 0]);
    assert.deepEqual([...(rec.lines[0].matrix as number[])].slice(12, 15), ORIGIN);
    assert.equal(rec.lines[0].onTop, true, "drawn through the member it runs inside");
    // A label per member and per weld, in world space, a constant size on screen.
    assert.equal(rec.labels.length, 3);
    assert.deepEqual(rec.labels[0].position, [10.8, 20, 30]);
    assert.deepEqual(rec.labels[1].position, [10, 21, 30]);
    assert.deepEqual(rec.labels[2].position, [10, 20, 31]);
    assert.ok(rec.labels.every((b) => b.sizing === "screen"));
    // Drawn again, the old ones go first.
    renderConnectionOverlay(PREVIEW, result, { ...ALL, welds: false });
    assert.equal(rec.labels.length, 2);
    clearConnectionOverlay();
    assert.equal(rec.lines.length + rec.labels.length, 0);
  } finally {
    withdraw();
  }
});

test("nothing is drawn while the preview is not on screen in the viewport, or with no viewport", () => {
  const rec = recordingViewport(new Set());
  const withdraw = setActiveViewport(rec.viewport);
  try {
    // The three.js-free viewport takes the model after the load returns: not there yet.
    assert.equal(renderConnectionOverlay(PREVIEW, result, ALL), 0);
    assert.equal(rec.lines.length + rec.labels.length, 0);
  } finally {
    withdraw();
  }
  assert.equal(renderConnectionOverlay(PREVIEW, result, ALL), 0);
});

test("the session draws again when the preview arrives on screen or another viewport takes over", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../../services/devConnections/connectionBuilderSession.ts", import.meta.url), "utf8");
  assert.match(src, /onModelLoaded\(\(info\) => \{\s*if \(info\.sourceName === previewOnShow\(\)\)/);
  assert.match(src, /onActiveViewportChange\(\(\) => drawOverlay\(\)\)/);
  const overlay = await readFile(new URL("../../utils/scene/connectionOverlay.ts", import.meta.url), "utf8");
  assert.ok(!/from "three"/.test(overlay), "no three.js scene of its own");
});
