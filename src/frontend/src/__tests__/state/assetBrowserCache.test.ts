/**
 * The browser-storage cache behind the Assets tab's instant open: it round-trips one tree per
 * scope, keeps no personal scope, refuses a malformed entry, and survives storage that throws.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { localAssetTreeCache } from "../../state/assetBrowserCache";

const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

function install(store: Storage | (() => never)): void {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get: typeof store === "function" ? store : () => store,
  });
}

function memoryStorage(quota = Infinity): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    key: (i) => [...m.keys()][i] ?? null,
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => {
      const used = [...m.entries()].reduce((n, [kk, vv]) => (kk === k ? n : n + vv.length), 0);
      if (used + v.length > quota) throw new Error("QuotaExceededError");
      m.set(k, v);
    },
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
  };
}

afterEach(() => {
  if (original) Object.defineProperty(globalThis, "localStorage", original);
  else delete (globalThis as { localStorage?: unknown }).localStorage;
});

const INDEX = JSON.stringify({ collections: { a: [] }, malformed: [] });

test("round-trips one tree per scope", () => {
  install(memoryStorage());
  localAssetTreeCache.write("shared", ["a", "b"], "a", INDEX);
  assert.deepEqual(localAssetTreeCache.read("shared"), {
    collections: ["a", "b"],
    collection: "a",
    index: { collections: { a: [] }, malformed: [] },
  });
  assert.equal(localAssetTreeCache.read("project:p"), null);
  localAssetTreeCache.forget("shared");
  assert.equal(localAssetTreeCache.read("shared"), null);
});

test("keeps nothing for a personal scope", () => {
  const storage = memoryStorage();
  install(storage);
  localAssetTreeCache.write("user:me", ["a"], "a", INDEX);
  assert.equal(storage.length, 0);
  assert.equal(localAssetTreeCache.read("user:me"), null);
});

test("a malformed entry reads as nothing", () => {
  const storage = memoryStorage();
  install(storage);
  storage.setItem("ada.assets.lastTree.v1.shared", '{"collections":"a"}');
  assert.equal(localAssetTreeCache.read("shared"), null);
  storage.setItem("ada.assets.lastTree.v1.shared", "{not json");
  assert.equal(localAssetTreeCache.read("shared"), null);
});

test("over quota, other scopes' entries make room", () => {
  const entry = (n: number) => JSON.stringify({ collections: { a: [] }, malformed: [], pad: "x".repeat(n) });
  install(memoryStorage(400));
  localAssetTreeCache.write("project:p", ["a"], "a", entry(200));
  localAssetTreeCache.write("shared", ["a"], "a", entry(200));
  assert.equal(localAssetTreeCache.read("project:p"), null);
  assert.ok(localAssetTreeCache.read("shared"));
});

test("storage that throws is no storage", () => {
  install(() => {
    throw new Error("SecurityError");
  });
  localAssetTreeCache.write("shared", ["a"], "a", INDEX);
  assert.equal(localAssetTreeCache.read("shared"), null);
  localAssetTreeCache.forget("shared");
});
