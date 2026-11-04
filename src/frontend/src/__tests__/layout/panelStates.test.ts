import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { watchStores } from "@/layout/bindings";
import type { PanelBinding } from "@/layout/panel";
import { panelStates, panelStatesKey, subscribePanels } from "@/layout/panelStates";
import { getPanel, registerPanel, resetPanelRegistry } from "@/layout/registry";

// What a host re-renders on: a binding's flag or a store an `available` reads.

afterEach(() => resetPanelRegistry());

function tinyStore<T>(state: T) {
  const subs = new Set<() => void>();
  return {
    state,
    getState: () => state,
    set(next: T) {
      state = next;
      subs.forEach((cb) => cb());
    },
    subscribe(cb: () => void) {
      subs.add(cb);
      return () => subs.delete(cb);
    },
  };
}

function binding(store: ReturnType<typeof tinyStore<{ open: boolean }>>): PanelBinding {
  return {
    isOpen: () => store.getState().open,
    setOpen: (open) => store.set({ open }),
    subscribe: (cb) => store.subscribe(cb),
  };
}

const body = () => Promise.resolve({ default: () => null });

test("states follow the controller's open answer and each panel's availability", () => {
  const gate = tinyStore({ on: true });
  registerPanel({ id: "a", title: "A", body, available: () => gate.getState().on }, "core");
  registerPanel({ id: "b", title: "B", body, available: (env) => env.restMode === true }, "core");
  const panels = [getPanel("a")!, getPanel("b")!];
  const open = new Set(["b"]);
  const states = panelStates(panels, { restMode: false }, { isOpen: (id) => open.has(id) });
  assert.deepEqual(states.get("a"), { open: false, available: true });
  assert.deepEqual(states.get("b"), { open: true, available: false });
  assert.equal(panelStatesKey(states), "a=01;b=10;");
});

test("the key changes exactly when a state does", () => {
  const s1 = new Map([["a", { open: true, available: true }]]);
  const s2 = new Map([["a", { open: true, available: true }]]);
  const s3 = new Map([["a", { open: false, available: true }]]);
  assert.equal(panelStatesKey(s1), panelStatesKey(s2));
  assert.notEqual(panelStatesKey(s1), panelStatesKey(s3));
});

test("subscribePanels hears bindings and watched stores, and unsubscribes them all", () => {
  const flagStore = tinyStore({ open: false });
  const gate = tinyStore({ on: true });
  registerPanel({ id: "a", title: "A", body, binding: binding(flagStore), watch: watchStores(gate) }, "core");
  let calls = 0;
  const extra = tinyStore({});
  const off = subscribePanels([getPanel("a")!], () => calls++, [(cb) => extra.subscribe(cb)]);
  flagStore.set({ open: true });
  gate.set({ on: false });
  extra.set({});
  assert.equal(calls, 3);
  off();
  flagStore.set({ open: false });
  gate.set({ on: true });
  assert.equal(calls, 3);
});
