import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { createLayoutController } from "@/layout/controller";
import type { ZoneDef } from "@/layout/layout";
import { createLayoutStore } from "@/layout/layoutStore";
import type { PanelBinding, PanelSpec } from "@/layout/panel";
import { registerPanel, resetPanelRegistry } from "@/layout/registry";
import { hide, isPanelOpen, reveal, setPanelController, toggle } from "@/layout/reveal";

// The controller a layout host installs: bound panels through their flag,
// unbound ones in the layout document, exclusive groups either way.

const ZONES: ZoneDef[] = [
  { id: "col", kind: "stack", label: "Column" },
  { id: "win", kind: "float", label: "Windows" },
];

const body = () => Promise.resolve({ default: () => null });

function flag(initial = false): PanelBinding & { value: boolean; writes: boolean[] } {
  const subs = new Set<() => void>();
  const b = {
    value: initial,
    writes: [] as boolean[],
    isOpen: () => b.value,
    setOpen: (open: boolean) => {
      b.writes.push(open);
      b.value = open;
      subs.forEach((cb) => cb());
    },
    subscribe: (cb: () => void) => {
      subs.add(cb);
      return () => subs.delete(cb);
    },
  };
  return b;
}

const spec = (id: string, extra: Partial<PanelSpec> = {}): PanelSpec => ({
  id,
  title: id,
  body,
  placement: { p: { zone: "col", order: 10 } },
  ...extra,
});

let store = createLayoutStore();

beforeEach(() => {
  resetPanelRegistry();
  store = createLayoutStore();
  setPanelController(createLayoutController({ preset: "p", zones: ZONES, store }));
});

afterEach(() => {
  setPanelController(null);
  resetPanelRegistry();
});

test("a bound panel opens and closes through its flag, and nothing is written to the document", () => {
  const b = flag();
  registerPanel(spec("core:a", { binding: b }), "core");
  assert.equal(reveal("core:a"), true);
  assert.equal(b.value, true);
  assert.equal(isPanelOpen("core:a"), true);
  assert.equal(toggle("core:a"), true);
  assert.equal(b.value, false);
  assert.deepEqual(store.getState().prefs.docs, {});
});

test("an unbound panel opens in its placement zone of the document", () => {
  registerPanel(spec("x:b", { placement: { p: { zone: "col", order: 20 } } }), "x");
  registerPanel(spec("x:a", { placement: { p: { zone: "col", order: 10 } } }), "x");
  reveal("x:b");
  reveal("x:a");
  const doc = store.getState().doc("p", ZONES);
  assert.deepEqual(doc.zones.col.panels, ["x:a", "x:b"], "in placement order");
  assert.equal(isPanelOpen("x:a"), true);
  hide("x:a");
  assert.equal(isPanelOpen("x:a"), false);
  assert.equal(hide("x:a"), true, "hiding a closed panel is fine");
});

test("an unbound panel placed in a float zone opens as a float of its preferred size", () => {
  registerPanel(spec("x:w", { placement: { p: { zone: "win", float: { w: 500, h: 300 } } } }), "x");
  reveal("x:w");
  const f = store.getState().doc("p", ZONES).floats["x:w"];
  assert.equal(f.w, 500);
  assert.equal(f.h, 300);
});

test("revealing one panel of an exclusive group closes the others, bound or not", () => {
  const admin = flag(true);
  registerPanel(spec("core:admin", { binding: admin, constraints: { exclusiveGroup: "w" } }), "core");
  registerPanel(spec("x:tool", { constraints: { exclusiveGroup: "w" }, placement: { p: { zone: "win" } } }), "x");
  reveal("x:tool");
  assert.equal(admin.value, false);
  assert.equal(isPanelOpen("x:tool"), true);
  reveal("core:admin");
  assert.equal(isPanelOpen("x:tool"), false);
  assert.equal(admin.value, true);
});

test("a panel hosted inside another, or placed in no zone of the preset, cannot be revealed on its own", () => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    registerPanel(spec("x:tab", { placement: { p: { zone: "col", parent: "core:a", as: "tab" } } }), "x");
    registerPanel(spec("x:elsewhere", { placement: { other: { zone: "col" } } }), "x");
    assert.equal(reveal("x:tab"), false);
    assert.equal(reveal("x:elsewhere"), false);
    assert.equal(reveal("x:unknown"), false);
  } finally {
    console.warn = warn;
  }
});
