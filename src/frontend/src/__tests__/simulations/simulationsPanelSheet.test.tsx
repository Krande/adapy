// The Robotic Simulations panel's classic frame is a bottom sheet on phones, like the Scene
// panel's: docked to the bottom edge with a grab handle, the heading pinned and the body
// scrolling under it -- and from `sm` up the menu-column card it always was.

import assert from "node:assert/strict";
import { test } from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SIMULATIONS_FRAME_CLASS, SimulationsFrame } from "@/components/simulations/SimulationsFrame";

const noop = () => {};

const classes = (s: string) => new Set(s.split(/\s+/).filter(Boolean));

test("on a phone the frame is a bottom sheet; from sm up, the card it was", () => {
  const c = classes(SIMULATIONS_FRAME_CLASS);
  for (const k of ["fixed", "inset-x-0", "bottom-0", "z-30", "w-full", "max-h-[82vh]", "rounded-t-2xl", "overflow-hidden"]) {
    assert.ok(c.has(k), k);
  }
  // The desktop card, exactly as before the sheet: in the column, translucent, 300-420 px wide.
  for (const k of ["sm:static", "sm:z-auto", "sm:w-auto", "sm:max-h-none", "sm:p-2", "sm:bg-gray-900/70", "sm:rounded-md",
    "sm:min-w-[300px]", "sm:max-w-[420px]", "sm:overflow-visible"]) {
    assert.ok(c.has(k), k);
  }
});

test("the frame draws the grab handle (phones only) above its pinned heading", () => {
  const html = renderToStaticMarkup(
    <SimulationsFrame header={<span>Robotic Simulations</span>} onDismiss={noop}>
      <p>body</p>
    </SimulationsFrame>,
  );
  assert.match(html, /data-simulations-panel/);
  const handle = html.indexOf('aria-label="Drag to resize the panel"');
  const title = html.indexOf("Robotic Simulations");
  assert.ok(handle > 0 && title > handle, "the handle comes first, then the heading");
  assert.match(html, /class="sm:hidden -mb-2 shrink-0 flex justify-center/);
  // The body scrolls within the sheet on a phone, and the runs list only scrolls by itself from sm up.
  assert.match(html, /flex-1 min-h-0 overflow-y-auto overscroll-contain sm:overflow-visible/);
});
