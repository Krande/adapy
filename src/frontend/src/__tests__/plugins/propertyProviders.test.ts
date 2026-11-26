// applyProviderLayout (a shell's static show/hide/order over the Selected Object panel's
// providers) and the provider-id ownership rule in registerPropertyProvider/providersFor.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyProviderLayout,
  listPropertyProviders,
  providersFor,
  registerPropertyProvider,
  type PropertyProvider,
  type SelectionSnapshot,
} from "@/plugins/propertyProviders";

const none: SelectionSnapshot = { kind: "none", name: null, meshName: null, count: 0, hasEntities: false, cellBuilderActive: false };

const p = (id: string, order: number): PropertyProvider => ({ id, order, match: () => true, render: () => null });

test("applyProviderLayout: absent layout returns a fresh array, unchanged order", () => {
  const list = [p("a", 0), p("b", 10)];
  const out = applyProviderLayout(list);
  assert.deepEqual(out.map((x) => x.id), ["a", "b"]);
  assert.notEqual(out, list, "a fresh array, like every other list here");
});

test("applyProviderLayout: show keeps only the named ids, in their original relative order", () => {
  const list = [p("a", 0), p("b", 10), p("c", 20)];
  assert.deepEqual(applyProviderLayout(list, { show: ["c", "a"] }).map((x) => x.id), ["a", "c"]);
});

test("applyProviderLayout: hide drops the named ids, applied after show", () => {
  const list = [p("a", 0), p("b", 10), p("c", 20)];
  assert.deepEqual(applyProviderLayout(list, { hide: ["b"] }).map((x) => x.id), ["a", "c"]);
  assert.deepEqual(applyProviderLayout(list, { show: ["a", "b", "c"], hide: ["b"] }).map((x) => x.id), ["a", "c"]);
});

test("applyProviderLayout: order overrides specific ids' order, stably; an unlisted id keeps its own", () => {
  const list = [p("a", 0), p("b", 10), p("c", 20)];
  // Move "c" before everything; "a" and "b" keep their own order (0, 10).
  assert.deepEqual(applyProviderLayout(list, { order: { c: -5 } }).map((x) => x.id), ["c", "a", "b"]);
  // Tie on order: original relative order wins.
  assert.deepEqual(applyProviderLayout(list, { order: { a: 10, b: 10 } }).map((x) => x.id), ["a", "b", "c"]);
});

test("applyProviderLayout: an id in show/hide/order that is not in the list has no effect", () => {
  const list = [p("a", 0), p("b", 10)];
  assert.deepEqual(applyProviderLayout(list, { show: ["a", "b", "ghost"] }).map((x) => x.id), ["a", "b"]);
  assert.deepEqual(applyProviderLayout(list, { hide: ["ghost"] }).map((x) => x.id), ["a", "b"]);
  assert.deepEqual(applyProviderLayout(list, { order: { ghost: -100 } }).map((x) => x.id), ["a", "b"]);
});

test("registerPropertyProvider: the same owner replaces an id; a different owner is refused and warned, its unregister a no-op", () => {
  const origWarn = console.warn;
  const warnings: unknown[][] = [];
  console.warn = (...args: unknown[]) => void warnings.push(args);
  try {
    registerPropertyProvider({ id: "shared", owner: "plugin-a", match: () => true, render: () => "a" as never });
    assert.equal(listPropertyProviders().find((x) => x.id === "shared")?.owner, "plugin-a");

    // Same owner: replaces. The LATEST registration's own unregister is now the live one -- an
    // earlier registration's unregister, once superseded (by the same owner or any other), is a
    // no-op (it only ever removes the entry it itself installed, still current).
    const offLatest = registerPropertyProvider({ id: "shared", owner: "plugin-a", match: () => true, render: () => "updated" as never });
    assert.equal((listPropertyProviders().find((x) => x.id === "shared")?.render(none)) as unknown, "updated");

    // A different owner: refused, warned, no-op unregister.
    warnings.length = 0;
    const offB = registerPropertyProvider({ id: "shared", owner: "plugin-b", match: () => true, render: () => "intruder" as never });
    assert.equal((listPropertyProviders().find((x) => x.id === "shared")?.render(none)) as unknown, "updated", "plugin-a keeps it");
    assert.ok(warnings.length > 0, "a one-time warning is logged");
    offB(); // no-op: must not remove plugin-a's registration
    assert.equal(listPropertyProviders().find((x) => x.id === "shared")?.owner, "plugin-a");

    offLatest();
    assert.equal(listPropertyProviders().find((x) => x.id === "shared"), undefined);
  } finally {
    console.warn = origWarn;
  }
});

test("registerPropertyProvider: no owner (undefined) on both sides counts as the SAME owner (\"\")", () => {
  registerPropertyProvider({ id: "anon", match: () => true, render: () => null });
  const off2 = registerPropertyProvider({ id: "anon", match: () => true, render: () => "replaced" as never });
  try {
    assert.equal((listPropertyProviders().find((x) => x.id === "anon")?.render(none)) as unknown, "replaced");
  } finally {
    off2();
  }
});

test("providersFor: a plugin provider using a core id is dropped unless its own owner is \"core\", warned once", () => {
  const origWarn = console.warn;
  const warnings: unknown[][] = [];
  console.warn = (...args: unknown[]) => void warnings.push(args);
  const core = [p("core-id", 0)];
  try {
    const offForeign = registerPropertyProvider({ id: "core-id", owner: "plugin-a", match: () => true, render: () => null });
    try {
      warnings.length = 0;
      const ids = providersFor(none, core).map((x) => x.id);
      assert.deepEqual(ids, ["core-id"], "core's own section draws there, the plugin's is dropped");
      assert.equal(warnings.length, 1);
      // A second pass does not warn again for the same id.
      providersFor(none, core);
      assert.equal(warnings.length, 1, "warned once, not every pass");
    } finally {
      offForeign();
    }

    const offCoreOwned = registerPropertyProvider({ id: "core-id-2", owner: "core", match: () => true, render: () => "mine" as never });
    try {
      const core2 = [p("core-id-2", 0)];
      const list = providersFor(none, core2);
      // Both core's own entry (from `core2`) and the "core"-owned plugin registration share the
      // id: providersFor does not de-duplicate within its own merge, it only drops a FOREIGN
      // owner -- so with owner "core" the registration is kept alongside core's.
      assert.ok(list.some((x) => x.id === "core-id-2"));
    } finally {
      offCoreOwned();
    }
  } finally {
    console.warn = origWarn;
  }
});
