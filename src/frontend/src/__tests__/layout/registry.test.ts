// The panel registry and the panel intents, on their own: registration and
// lookup, availability, toolbar order, and reveal / hide / toggle delegating to
// a panel's binding. Dependency-free, like the modules under test.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { bindStoreFlag, type FlagStore } from "@/layout/bindings";
import { DEFAULT_PANEL_CAPABILITIES, placementFor, type PanelSpec } from "@/layout/panel";
import {
  getPanel,
  listPanels,
  listToolbarPanels,
  registerPanel,
  removePanelsOwnedBy,
  resetPanelRegistry,
} from "@/layout/registry";
import {
  bindingController,
  hide,
  isPanelOpen,
  reveal,
  setPanelController,
  toggle,
} from "@/layout/reveal";

/** A minimal zustand-shaped store. */
function flagStore<S extends object>(initial: S): FlagStore<S> & { setState(p: Partial<S>): void; writes: number } {
  let state = initial;
  const listeners = new Set<(s: S, p: S) => void>();
  const store = {
    writes: 0,
    getState: () => state,
    setState(p: Partial<S>) {
      const prev = state;
      state = { ...state, ...p };
      store.writes += 1;
      for (const l of listeners) l(state, prev);
    },
    subscribe(l: (s: S, p: S) => void) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
  return store;
}

const body = async () => ({ default: () => null });

function spec(id: string, extra: Partial<PanelSpec> = {}): PanelSpec {
  return { id, title: id, body, ...extra };
}

beforeEach(() => {
  resetPanelRegistry();
  setPanelController(null);
});

test("registers and looks up a panel, filling capability defaults and the frame", () => {
  registerPanel(spec("core:a", { capabilities: { popout: true } }), "core");
  const p = getPanel("core:a");
  assert.ok(p);
  assert.equal(p.owner, "core");
  assert.equal(p.frame, "own");
  assert.deepEqual(p.capabilities, { ...DEFAULT_PANEL_CAPABILITIES, popout: true });
  assert.equal(getPanel("core:missing"), undefined);
});

test("first writer wins on a duplicate id; a spec without id or body is ignored", () => {
  registerPanel(spec("core:a", { title: "first" }), "core");
  registerPanel(spec("core:a", { title: "second" }), "other");
  registerPanel(spec(""), "core");
  registerPanel({ id: "core:nobody", title: "x" } as unknown as PanelSpec, "core");
  assert.deepEqual(
    listPanels().map((p) => [p.id, p.title, p.owner]),
    [["core:a", "first", "core"]],
  );
});

test("listPanels keeps registration order and filters by available(env)", () => {
  registerPanel(spec("core:rest", { available: (env) => env.restMode !== false }), "core");
  registerPanel(spec("core:always"), "core");
  registerPanel(spec("core:throws", { available: () => { throw new Error("boom"); } }), "core");
  assert.deepEqual(listPanels().map((p) => p.id), ["core:rest", "core:always", "core:throws"]);
  assert.deepEqual(listPanels({ restMode: false }).map((p) => p.id), ["core:always"]);
  assert.deepEqual(listPanels({ restMode: true }).map((p) => p.id), ["core:rest", "core:always"]);
});

test("toolbar panels sort by order, then subOrder, then id", () => {
  registerPanel(spec("p:b", { toolbar: { order: 500, subOrder: 1 } }), "p");
  registerPanel(spec("core:end", { toolbar: { order: 1000 } }), "core");
  registerPanel(spec("p:a", { toolbar: { order: 500, subOrder: 1 } }), "p");
  registerPanel(spec("p:first", { toolbar: { order: 500, subOrder: -5 } }), "p");
  registerPanel(spec("core:start", { toolbar: { order: 10 } }), "core");
  registerPanel(spec("core:none"), "core");
  assert.deepEqual(
    listToolbarPanels().map((p) => p.id),
    ["core:start", "p:first", "p:a", "p:b", "core:end"],
  );
});

test("removePanelsOwnedBy drops exactly one owner's panels", () => {
  registerPanel(spec("core:a"), "core");
  registerPanel(spec("p:a"), "p");
  removePanelsOwnedBy("p");
  assert.deepEqual(listPanels().map((p) => p.id), ["core:a"]);
});

test("placementFor prefers the preset's own entry, then default", () => {
  const p = spec("x", {
    placement: { classic: { zone: "classic.menu-column" }, default: { zone: "right" } },
  });
  assert.equal(placementFor(p, "classic")?.zone, "classic.menu-column");
  assert.equal(placementFor(p, "docked")?.zone, "right");
  assert.equal(placementFor(spec("y"), "classic"), undefined);
});

test("reveal / hide / toggle write the bound flag through the store's own setter", () => {
  const store = flagStore({ shown: false });
  const setter: boolean[] = [];
  registerPanel(
    spec("core:a", {
      binding: bindStoreFlag(
        store,
        (s) => s.shown,
        (open) => {
          setter.push(open);
          store.setState({ shown: open });
        },
      ),
    }),
    "core",
  );
  assert.equal(isPanelOpen("core:a"), false);
  assert.equal(reveal("core:a"), true);
  assert.equal(store.getState().shown, true);
  assert.equal(isPanelOpen("core:a"), true);
  assert.equal(toggle("core:a"), true);
  assert.equal(store.getState().shown, false);
  assert.equal(toggle("core:a"), true);
  assert.equal(store.getState().shown, true);
  assert.equal(hide("core:a"), true);
  assert.equal(store.getState().shown, false);
  // Every intent reaches the setter, as the `setX(true)` call it replaces did.
  assert.equal(reveal("core:a"), true);
  assert.equal(reveal("core:a"), true);
  assert.deepEqual(setter, [true, false, true, false, true, true]);
});

test("a binding's subscribe fires only when the flag changes", () => {
  const store = flagStore({ shown: false, other: 0 });
  const b = bindStoreFlag(store, (s) => s.shown, (open) => store.setState({ shown: open }));
  let calls = 0;
  const off = b.subscribe(() => (calls += 1));
  store.setState({ other: 1 });
  assert.equal(calls, 0);
  b.setOpen(true);
  assert.equal(calls, 1);
  b.setOpen(true);
  assert.equal(calls, 1);
  off();
  b.setOpen(false);
  assert.equal(calls, 1);
});

test("intents on an unknown or unbound panel are a no-op returning false", () => {
  registerPanel(spec("core:unbound"), "core");
  assert.equal(reveal("core:nope"), false);
  assert.equal(hide("core:unbound"), false);
  assert.equal(toggle("core:unbound"), false);
  assert.equal(isPanelOpen("core:unbound"), false);
});

test("a layout can install its own controller, and remove it again", () => {
  const store = flagStore({ shown: false });
  registerPanel(
    spec("core:a", {
      binding: bindStoreFlag(store, (s) => s.shown, (open) => store.setState({ shown: open })),
    }),
    "core",
  );
  const seen: string[] = [];
  setPanelController({
    isOpen: () => false,
    reveal: (id) => (seen.push(`reveal ${id}`), true),
    hide: (id) => (seen.push(`hide ${id}`), true),
  });
  reveal("core:a");
  toggle("core:a");
  assert.deepEqual(seen, ["reveal core:a", "reveal core:a"]);
  assert.equal(store.getState().shown, false);
  setPanelController(null);
  reveal("core:a");
  assert.equal(store.getState().shown, true);
  assert.equal(bindingController.isOpen("core:a"), true);
});
