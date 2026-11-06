import assert from "node:assert/strict";
import { test } from "node:test";

import type { LayoutPrefs } from "@/layout/layout";
import { LAYOUT_PREFS_VERSION, createLayoutStore, memoryLayoutPersistence } from "@/layout/layoutStore";
import { LAYOUT_PREFERENCE_KEY, serverLayoutPersistence, type PreferenceStore } from "@/layout/serverPersistence";

const prefs = (extra: Partial<LayoutPrefs> = {}): LayoutPrefs => ({ v: LAYOUT_PREFS_VERSION, docs: {}, ...extra });

function fakeServer(initial: unknown = null) {
  const s = {
    value: initial,
    puts: [] as { key: string; value: unknown; keepalive?: boolean }[],
    fail: false,
    async get(key: string) {
      assert.equal(key, LAYOUT_PREFERENCE_KEY);
      if (s.fail) throw new Error("503");
      return s.value;
    },
    async put(key: string, value: unknown, opts?: { keepalive?: boolean }) {
      if (s.fail) throw new Error("503");
      s.puts.push({ key, value, keepalive: opts?.keepalive });
      s.value = value;
    },
  };
  return s satisfies PreferenceStore;
}

const quiet = async <T>(fn: () => Promise<T>): Promise<T> => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    return await fn();
  } finally {
    console.warn = warn;
  }
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

test("loads the server's prefs", async () => {
  const server = fakeServer(prefs({ customisablePanels: true }));
  const p = serverLayoutPersistence(server, { fallback: memoryLayoutPersistence(prefs({ activePreset: "x" })) });
  assert.deepEqual(await p.load(), prefs({ customisablePanels: true }));
});

test("with nothing on the server yet, the browser's prefs are adopted", async () => {
  const p = serverLayoutPersistence(fakeServer(null), { fallback: memoryLayoutPersistence(prefs({ activePreset: "x" })) });
  assert.deepEqual(await p.load(), prefs({ activePreset: "x" }));
  assert.equal(await serverLayoutPersistence(fakeServer(null)).load(), null);
});

test("a burst of saves sends only the last, once the debounce runs out", async () => {
  const server = fakeServer();
  const p = serverLayoutPersistence(server, { debounceMs: 10 });
  await p.save(prefs({ activePreset: "a" }));
  await p.save(prefs({ activePreset: "b" }));
  await p.save(prefs({ activePreset: "c" }));
  assert.equal(server.puts.length, 0);
  await sleep(30);
  assert.deepEqual(
    server.puts.map((x) => (x.value as LayoutPrefs).activePreset),
    ["c"],
  );
});

test("flush sends a pending save at once, and nothing when none is pending", async () => {
  const server = fakeServer();
  const p = serverLayoutPersistence(server, { debounceMs: 10_000 });
  await p.save(prefs({ activePreset: "a" }));
  await p.flush({ keepalive: true });
  assert.deepEqual(server.puts, [{ key: LAYOUT_PREFERENCE_KEY, value: prefs({ activePreset: "a" }), keepalive: true }]);
  await p.flush();
  assert.equal(server.puts.length, 1);
});

test("a server that keeps no preferences hands loading and saving to the fallback", async () => {
  const server = fakeServer();
  server.fail = true;
  const local = memoryLayoutPersistence(prefs({ activePreset: "local" }));
  const p = serverLayoutPersistence(server, { fallback: local, debounceMs: 0 });
  assert.deepEqual(await quiet(() => p.load()), prefs({ activePreset: "local" }));
  await p.save(prefs({ activePreset: "next" }));
  await p.flush();
  assert.deepEqual(local.saved, prefs({ activePreset: "next" }));
});

test("a failed save falls back for the rest of the page", async () => {
  const server = fakeServer();
  const local = memoryLayoutPersistence();
  const p = serverLayoutPersistence(server, { fallback: local, debounceMs: 10_000 });
  server.fail = true;
  await p.save(prefs({ activePreset: "a" }));
  await quiet(() => p.flush());
  assert.equal(local.saved?.activePreset, "a");
  server.fail = false;
  await p.save(prefs({ activePreset: "b" }));
  await p.flush();
  assert.equal(server.puts.length, 0);
  assert.equal(local.saved?.activePreset, "b");
});

test("the layout store hydrates from the server and saves its changes there", async () => {
  const server = fakeServer(prefs({ customisablePanels: true }));
  const p = serverLayoutPersistence(server, { debounceMs: 10_000 });
  const store = createLayoutStore();
  await store.getState().hydrate(p);
  assert.equal(store.getState().prefs.customisablePanelsByShell?.core, true, "the setting of before is core's own UI's");
  store.getState().setCustomisablePanels(false);
  store.getState().setActivePreset("docked");
  await p.flush();
  assert.deepEqual(server.puts.length, 1);
  assert.deepEqual(server.value, prefs({ customisablePanelsByShell: { core: false }, activePreset: "docked" }));
});
