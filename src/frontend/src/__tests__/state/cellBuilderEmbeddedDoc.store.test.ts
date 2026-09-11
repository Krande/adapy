import assert from "node:assert/strict";
import { test } from "node:test";

// A procedural document that arrived embedded in a GLB (`assembly.show()`, the
// websocket/desktop path) must be BROWSABLE without being EDITABLE.
//
// The two halves are deliberately separate questions, and this pins why:
//
//   `active`         -- an editable session with somewhere to commit back to.
//                       `CellBuilderController` gates every editing interaction
//                       on it (gizmos, click-to-place, drag-to-move) and
//                       `setupCameraControlsHandlers` auto-compiles when it is
//                       set, so `loadFromDoc` must NOT set it. Editing stays off
//                       for free, which is the whole reason for the split.
//   `hasEmbeddedDoc` -- there is a document worth showing and no session behind
//                       it. Drives panel + menu-button visibility only. DERIVED
//                       from `active` + `cells` + `systems`, never stored: a
//                       parallel flag would have to be kept in step by every
//                       action that touches those.
//
// Conflating them (a synthetic `active` for embedded docs) would silently switch
// on the entire 3D editing surface in a viewer with nowhere to save.

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

const DOC = {
  spaces: [{ NAME: "Deck1", X: 0, Y: 0, Z: 0, DX: 24, DY: 12, DZ: 5 }],
  equipments: [
    {
      NAME: "V-201",
      DESCRIPTION: "separator",
      SPACE_NAME: "Deck1",
      X: 1,
      Y: 1,
      Z: 0,
      LX: 5,
      LY: 2.5,
      LZ: 2.5,
    },
  ],
  systems: [{ NAME: "201/1", TYPE: "piping", MEDIUM: "HC", CONNECTIONS: [] }],
};

test("an embedded document is loaded for viewing without claiming a session", async () => {
  const { hasEmbeddedDoc, useCellBuilderStore } = await import("@/state/cellBuilderStore");

  useCellBuilderStore.setState({ active: null, cells: {}, systems: {} });
  assert.equal(hasEmbeddedDoc(useCellBuilderStore.getState()), false, "nothing loaded yet");
  useCellBuilderStore.getState().loadFromDoc(DOC as never);

  const s = useCellBuilderStore.getState();
  assert.equal(hasEmbeddedDoc(s), true, "the panel must have something to show");
  assert.equal(s.active, null, "there is nowhere to commit to; editing must stay off");
  assert.ok(Object.keys(s.cells).length > 0, "the equipment must have loaded");
  assert.ok(Object.keys(s.systems).length > 0, "the systems must have loaded");
});

test("an empty document hides the panel again rather than leaving an empty one", async () => {
  // setupModelLoader calls loadFromDoc with an empty doc to CLEAR the panels when
  // a model with no procedural provenance loads. Reporting an embedded document
  // there would strand an empty browser over an unrelated model.
  const { hasEmbeddedDoc, useCellBuilderStore } = await import("@/state/cellBuilderStore");

  useCellBuilderStore.getState().loadFromDoc(DOC as never);
  useCellBuilderStore.getState().loadFromDoc({ spaces: [], equipments: [] } as never);

  assert.equal(hasEmbeddedDoc(useCellBuilderStore.getState()), false);
});

test("opening a real model supersedes the view-only document", async () => {
  // Otherwise the header would go on calling an editable session "read-only".
  // Goes through `open()` itself: the derived answer must flip on the session
  // being set, with no flag for `open` to remember to clear.
  const { hasEmbeddedDoc, useCellBuilderStore } = await import("@/state/cellBuilderStore");

  useCellBuilderStore.getState().loadFromDoc(DOC as never);
  assert.equal(hasEmbeddedDoc(useCellBuilderStore.getState()), true);

  useCellBuilderStore.getState().open("m", "n", 3, DOC as never);

  const s = useCellBuilderStore.getState();
  assert.equal(hasEmbeddedDoc(s), false);
  assert.notEqual(s.active, null);
  assert.ok(Object.keys(s.cells).length > 0, "the session's own document is loaded");

  // And closing the session leaves nothing to show either.
  useCellBuilderStore.getState().close();
  assert.equal(hasEmbeddedDoc(useCellBuilderStore.getState()), false);
});

test("the REST transport is unaffected by the read-only gate", async () => {
  // The panel is shared with the hosted viewer, so the gate must be provably
  // inert there: REST reports canEdit, and an open session is not read-only.
  const { RESTProceduralModelCapability } = await import(
    "@/services/capabilities/rest_capabilities"
  );
  const { WSProceduralModelCapability } = await import(
    "@/services/capabilities/ws_capabilities"
  );

  // isReadOnly(s) === !s.active || !capabilities.procedural.canEdit
  const isReadOnly = (active: unknown | null, canEdit: boolean) => !active || !canEdit;

  const rest = new RESTProceduralModelCapability();
  const ws = new WSProceduralModelCapability();

  assert.equal(
    isReadOnly({ modelId: "m" }, rest.canEdit),
    false,
    "an open model over REST must stay fully editable",
  );
  assert.equal(
    isReadOnly(null, ws.canEdit),
    true,
    "an embedded doc over the websocket is read-only until a save verb exists",
  );
  // The seam is what flips: when a save verb lands over the websocket, canEdit
  // becomes true and every control gated on this returns, with no panel change.
  assert.equal(
    isReadOnly({ modelId: "m" }, true),
    false,
    "canEdit is the switch, not the transport name",
  );
});
