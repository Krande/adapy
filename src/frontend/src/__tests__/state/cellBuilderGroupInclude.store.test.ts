import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

// Store-level: a group's build inclusion (`include: false`) survives the real doc
// round-trip (loadFromDoc -> toDoc), and toggling it through the store is an
// undoable document edit that marks the model dirty -- unlike the display
// controls (eye / colour), which never touch the document.
//
// The store reads sessionStorage/localStorage at import (the auth layer): seed
// in-memory shims first, as cellBuilderMetadata.store.test.ts does.
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  clear(): void {
    this.m.clear();
  }
  key(i: number): string | null {
    return [...this.m.keys()][i] ?? null;
  }
  get length(): number {
    return this.m.size;
  }
}
const g = globalThis as Record<string, unknown>;
g.sessionStorage ??= new MemStorage();
g.localStorage ??= new MemStorage();

const doc: ProceduralDoc = {
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "deck", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "Mini" },
    { NAME: "leg", X: 0, Y: 0, Z: -20, DX: 4, DY: 4, DZ: 20, STRUCTURE_NAME: "Mini_jacket" },
  ],
  equipments: [],
  openings: [],
  groups: [
    { name: "Mini", blueprint: "Stiffened plate" },
    { name: "Mini_jacket", blueprint: "Framework", include: false },
  ],
};

test("include: false round-trips loadFromDoc -> toDoc; a built group carries no key", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  useCellBuilderStore.getState().loadFromDoc(doc);
  const out = useCellBuilderStore.getState().toDoc();
  assert.deepEqual(out.groups, doc.groups);
  assert.equal("include" in (out.groups ?? [])[0], false);
  // The cells keep their groups through the same trip.
  assert.deepEqual(
    (out.spaces ?? []).map((s) => s.STRUCTURE_NAME),
    ["Mini", "Mini_jacket"],
  );
});

test("setGroupIncluded is an undoable, dirtying document edit", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const st = () => useCellBuilderStore.getState();
  st().loadFromDoc(doc);
  assert.equal(st().dirty, false);

  st().setGroupIncluded("Mini_jacket", true);
  assert.equal(st().dirty, true);
  assert.deepEqual(st().toDoc().groups, [
    { name: "Mini", blueprint: "Stiffened plate" },
    { name: "Mini_jacket", blueprint: "Framework" },
  ]);

  st().setGroupIncluded("Mini", false);
  assert.deepEqual(
    (st().toDoc().groups ?? []).map((x) => x.include),
    [false, undefined],
  );

  st().undo();
  assert.deepEqual(
    (st().toDoc().groups ?? []).map((x) => x.include),
    [undefined, undefined],
  );

  // A no-op (already built) and an unknown group change nothing.
  const before = st().groups;
  st().setGroupIncluded("Mini", true);
  st().setGroupIncluded("nope", false);
  assert.equal(st().groups, before);
});
