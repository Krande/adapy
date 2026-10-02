// A saved group as a clash-check target: the body it puts on the wire (one addressing form), the
// store holding it, and the panel's follow-the-loaded-model effect NOT replacing it.
//
// Browser globals are stubbed before the dynamic import for the reason given in
// `parseClashResult.test.ts` -- `@/state/clashCheckStore` reaches `sessionStorage` at load.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { useClashCheckStore, sameClashTarget } = await import("@/state/clashCheckStore");
const { clashTargetBody, describeClashTarget } = await import("@/services/api/clashCheck");

const MEMBERS = [
  { target: { kind: "file", source_key: "proj/a.ifc" }, element: "Deck A", path: ["Site", "Deck A"] },
  {
    target: { kind: "node", provider: "prov", collection: "plant", subject: "S10", revision: "r1", node: null },
    element: null,
    path: [],
  },
] as const;

test("a group goes on the wire as {group: {name, members}} and nothing else", () => {
  const body = clashTargetBody({ kind: "group", name: "Decks", members: MEMBERS });
  assert.deepEqual(body, { group: { name: "Decks", members: MEMBERS } });
  assert.ok(!("source_key" in body) && !("collection" in body));
  assert.equal(describeClashTarget({ kind: "group", name: "Decks", members: MEMBERS }), "group Decks");
});

test("a group target survives the panel following the loaded model; choosing the file replaces it", () => {
  const s = useClashCheckStore.getState();
  s.reset();
  s.setGroupTarget({ name: "Decks", members: MEMBERS });
  assert.equal(useClashCheckStore.getState().assetTarget?.kind, "group");
  assert.equal(useClashCheckStore.getState().sourceKey, null);

  // The tab mounting (or another model loading) follows the scene: an explicit target stays.
  useClashCheckStore.getState().followLoadedSource("proj/b.ifc");
  assert.equal(useClashCheckStore.getState().assetTarget?.kind, "group");

  // "use loaded model" goes back to the file.
  useClashCheckStore.getState().setSource("proj/b.ifc", "proj/b.ifc");
  assert.equal(useClashCheckStore.getState().assetTarget, null);
  assert.equal(useClashCheckStore.getState().sourceKey, "proj/b.ifc");

  // With nothing explicit, following works as before.
  useClashCheckStore.getState().followLoadedSource("proj/c.ifc");
  assert.equal(useClashCheckStore.getState().sourceKey, "proj/c.ifc");
});

test("renaming a group keeps its result; changing its members does not", () => {
  const reversed = [...MEMBERS].reverse();
  assert.ok(sameClashTarget({ kind: "group", name: "A", members: MEMBERS }, { kind: "group", name: "B", members: reversed }));
  assert.ok(!sameClashTarget({ kind: "group", name: "A", members: MEMBERS }, { kind: "group", name: "A", members: MEMBERS.slice(1) }));
  assert.ok(!sameClashTarget({ kind: "file", sourceKey: "x" }, { kind: "group", name: "A", members: [] }));
  assert.ok(sameClashTarget({ kind: "node", collection: "c", subject: "s" }, { kind: "node", collection: "c", subject: "s", node: null }));
});
