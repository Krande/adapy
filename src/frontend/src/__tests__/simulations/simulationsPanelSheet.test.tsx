// The Robotic Simulations panel's classic frame is a bottom sheet on phones, like the Scene
// panel's: docked to the bottom edge with a grab handle, the heading pinned and the body
// scrolling under it -- and from `sm` up the menu-column card it always was.

import assert from "node:assert/strict";
import { test } from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SIMULATIONS_BODY_CLASS, SIMULATIONS_FRAME_CLASS, SimulationsFrame } from "@/components/simulations/SimulationsFrame";

const noop = () => {};

const classes = (s: string) => new Set(s.split(/\s+/).filter(Boolean));

test("on a phone the frame is a bottom sheet; from sm up, the card it was", () => {
  const c = classes(SIMULATIONS_FRAME_CLASS);
  for (const k of ["fixed", "inset-x-0", "bottom-0", "z-30", "w-full", "max-h-[82vh]", "rounded-t-2xl", "overflow-hidden"]) {
    assert.ok(c.has(k), k);
  }
  // On the active panel theme's colours at every width, never a fixed grey.
  for (const k of ["bg-[var(--ada-panel-bg)]", "text-[var(--ada-panel-text)]"]) assert.ok(c.has(k), k);
  assert.ok(![...c].some((k) => /(?:bg|text)-(?:gray|white)/.test(k)), "no fixed colour on the frame");
  // The desktop card: in the column, bordered like the other cards, 300-420 px wide.
  for (const k of ["sm:static", "sm:z-auto", "sm:w-auto", "sm:max-h-[calc(100dvh-5.5rem)]", "sm:p-2", "sm:rounded-md",
    "sm:border", "sm:border-[var(--ada-panel-border)]",
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
  // The body scrolls under the pinned heading on a phone and on desktop alike.
  assert.ok(html.includes(`class="${SIMULATIONS_BODY_CLASS}" data-simulations-body`), "the body is the scrolling one");
});

test("the body's scrollbar never covers its content (the Connections and Robotic Simulations panels)", () => {
  const c = classes(SIMULATIONS_BODY_CLASS);
  for (const k of ["flex-1", "min-h-0", "overflow-y-auto", "overscroll-contain"]) assert.ok(c.has(k), k);
  // The scrollbar sits in the frame's right padding (p-2 / px-2), outside the content: an overlay
  // scrollbar covered the rows' right edge while the body had no padding of its own.
  assert.ok(c.has("-mr-2") && c.has("pr-2"), "the body reaches into the frame's padding and keeps it as its own");
  const frame = classes(SIMULATIONS_FRAME_CLASS);
  assert.ok(frame.has("sm:p-2") && frame.has("px-2"), "the frame has that padding");
  // A classic scrollbar's room is kept whether or not the body overflows: no reflow as a tab grows.
  assert.ok(c.has("[scrollbar-gutter:stable]"));
});
