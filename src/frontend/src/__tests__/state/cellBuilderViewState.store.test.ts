import assert from "node:assert/strict";
import { test } from "node:test";

// Regression: closing/reopening a procedural model must reset the VIEW state to a
// clean topology view. A session left in a plain simulation view has cellsVisible
// = superimpose||sideBySide = false; if close() (and open()) don't reset that, the
// reopened model shows repMode "topology" but with its cells HIDDEN (empty view)
// until a repMode toggle re-runs setCellsVisible(true).
//
// The store reads sessionStorage/localStorage at import — seed in-memory shims
// first (no jsdom needed for a headless state check).
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

test("close() resets a result-view-tainted state to a clean topology view", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

  // Simulate a session left in a plain simulation view (cells hidden, modifiers
  // on). Stub the scene-touching hide* side effects so this stays headless.
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "n", revision: 0 },
    cellsVisible: false,
    superimpose: true,
    sideBySide: true,
    repMode: "simulation",
    hideResult: () => {},
    hideDetail: () => {},
  });

  useCellBuilderStore.getState().close();

  const st = useCellBuilderStore.getState();
  assert.equal(st.active, null);
  assert.equal(st.repMode, "topology");
  assert.equal(st.cellsVisible, true, "cells must be visible again on close");
  assert.equal(st.superimpose, false);
  assert.equal(st.sideBySide, false);
});
