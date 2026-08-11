import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

// A store-level test that a topology instance's user-defined METADATA survives
// the doc round-trip (loadFromDoc -> toDoc, i.e. cellsFromDoc -> toDoc) untouched
// for every box kind (space / equipment / opening) and that editing it through
// the store's existing withHistory action (setCellParam) is undoable + drops the
// key when the last field is removed.
//
// The store module reads sessionStorage/localStorage at import (the auth layer),
// so seed lightweight in-memory Storage shims BEFORE importing it — no jsdom /
// browser env needed for a headless round-trip.
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

test("METADATA round-trips through loadFromDoc -> toDoc for space/equipment/opening", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

  const doc: ProceduralDoc = {
    grid: {},
    blueprint: {},
    spaces: [
      {
        NAME: "C1",
        X: 0,
        Y: 0,
        Z: 0,
        DX: 1,
        DY: 1,
        DZ: 1,
        METADATA: { owner: "hull", frame: 12, checked: true },
      },
    ],
    equipments: [
      {
        NAME: "P1",
        SPACE_NAME: "C1",
        GLOBAL_COORDS: true,
        X: 0,
        Y: 0,
        Z: 0,
        LX: 1,
        LY: 1,
        LZ: 1,
        METADATA: { tag: "PU-001", nested: { a: 1 } },
      },
    ],
    openings: [
      {
        NAME: "O1",
        SUBTYPE: "door",
        USE_GLOBAL_COORDS: true,
        X: 0,
        Y: 0,
        Z: 0,
        DX: 1,
        DY: 2,
        DZ: 0.2,
        METADATA: { fireRated: "A60" },
      },
    ],
    systems: [],
  };

  useCellBuilderStore.getState().loadFromDoc(doc);
  const out = useCellBuilderStore.getState().toDoc();

  assert.deepEqual(out.spaces[0].METADATA, {
    owner: "hull",
    frame: 12,
    checked: true,
  });
  assert.deepEqual(out.equipments[0].METADATA, {
    tag: "PU-001",
    nested: { a: 1 },
  });
  const openings = out.openings ?? [];
  assert.deepEqual(openings[0].METADATA, { fireRated: "A60" });
});

test("setCellParam edits METADATA undoably and an empty map drops the key", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

  const doc: ProceduralDoc = {
    grid: {},
    blueprint: {},
    spaces: [{ NAME: "C1", X: 0, Y: 0, Z: 0, DX: 1, DY: 1, DZ: 1 }],
    equipments: [],
    openings: [],
    systems: [],
  };
  useCellBuilderStore.getState().loadFromDoc(doc);
  const st = () => useCellBuilderStore.getState();
  const cellId = Object.keys(st().cells)[0];

  // No METADATA yet -> not emitted.
  assert.equal(st().toDoc().spaces[0].METADATA, undefined);

  // Set a map through the existing withHistory action.
  st().setCellParam(cellId, "METADATA", { note: "hi", frame: 3 });
  assert.deepEqual(st().toDoc().spaces[0].METADATA, { note: "hi", frame: 3 });
  assert.equal(st().dirty, true);

  // Undo restores the pre-edit (absent) state.
  st().undo();
  assert.equal(st().toDoc().spaces[0].METADATA, undefined);

  // A null/empty value removes the key entirely (no METADATA={} in the doc).
  st().setCellParam(cellId, "METADATA", { a: 1 });
  st().setCellParam(cellId, "METADATA", null);
  assert.equal(st().toDoc().spaces[0].METADATA, undefined);
});
