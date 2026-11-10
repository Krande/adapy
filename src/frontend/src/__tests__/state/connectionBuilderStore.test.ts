/**
 * The connection builder's state (`ada dev connections`): a build sends the spec's defaults
 * overridden by what was edited, edits are kept per spec, and they are remembered per library
 * across a page reload -- or not at all when storage throws, without breaking the builder.
 */

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import type { DevConnectionsState } from "../../services/api/devConnections";
import { effectiveInputs, useConnectionBuilderStore } from "../../state/connectionBuilderStore";

const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

function install(get: () => Storage): void {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get });
}

function library(config = "/libs/a/ada_connections.toml"): DevConnectionsState {
  return {
    library: { name: "a", config, root: "/libs/a", watch: ["/libs/a"] },
    status: "ready",
    generation: 1,
    specs: [],
    error: null,
    changed: [],
    updated_at: 0,
  };
}

const initial = useConnectionBuilderStore.getState();

beforeEach(() => {
  useConnectionBuilderStore.setState(initial, true);
});

afterEach(() => {
  if (original) Object.defineProperty(globalThis, "localStorage", original);
  else delete (globalThis as { localStorage?: Storage }).localStorage;
});

test("a build sends the defaults, overridden field by field by what was edited", () => {
  const defaults = {
    incoming: { thickness: 0.02, width: 0.4, height: 0.3 },
    landing: { section: "BOX300x300x12x12", angle_deg: 90 },
  };
  assert.deepEqual(effectiveInputs(defaults, undefined), defaults);
  assert.deepEqual(effectiveInputs(defaults, { incoming: { thickness: 0.03 } }), {
    incoming: { thickness: 0.03, width: 0.4, height: 0.3 },
    landing: { section: "BOX300x300x12x12", angle_deg: 90 },
  });
});

test("edits are kept per spec, and reset per spec", () => {
  install(memoryStorage);
  const s = () => useConnectionBuilderStore.getState();
  s().setLibrary(library());
  s().setRoleField("box.a", "incoming", "section", "BOX200x200x10x10");
  s().setRoleField("box.a", "landing", "angle_deg", 60);
  s().setRoleField("box.b", "incoming", "thickness", 0.01);
  assert.deepEqual(s().inputsBySpec["box.a"], {
    incoming: { section: "BOX200x200x10x10" },
    landing: { angle_deg: 60 },
  });
  s().resetInputs("box.a");
  assert.equal(s().inputsBySpec["box.a"], undefined);
  assert.deepEqual(s().inputsBySpec["box.b"], { incoming: { thickness: 0.01 } });
});

test("the selection and edits come back for the same library after a reload, not for another", () => {
  const storage = memoryStorage();
  install(() => storage);
  const s = () => useConnectionBuilderStore.getState();
  s().setLibrary(library());
  s().selectSpec("box.a");
  s().setRoleField("box.a", "incoming", "section", "BOX200x200x10x10");

  useConnectionBuilderStore.setState(initial, true); // a page reload
  s().setLibrary(library());
  assert.equal(s().selectedSpec, "box.a");
  assert.deepEqual(s().inputsBySpec["box.a"], { incoming: { section: "BOX200x200x10x10" } });

  useConnectionBuilderStore.setState(initial, true);
  s().setLibrary(library("/libs/other/ada_connections.toml"));
  assert.equal(s().selectedSpec, null);
  assert.deepEqual(s().inputsBySpec, {});
});

test("storage that throws costs only the memory, not the builder", () => {
  install(() => {
    throw new Error("blocked");
  });
  const s = () => useConnectionBuilderStore.getState();
  s().setLibrary(library());
  s().selectSpec("box.a");
  s().setRoleField("box.a", "incoming", "thickness", 0.03);
  assert.equal(s().selectedSpec, "box.a");
  assert.deepEqual(s().inputsBySpec["box.a"], { incoming: { thickness: 0.03 } });
});
