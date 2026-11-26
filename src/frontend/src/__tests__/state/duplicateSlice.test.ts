// Shift+D duplicates the current selection -- cells, loft members (whole) and a plugin's refs
// together -- selects the copies and arms the move gizmo, one undo step. Bare D keeps its own
// meaning (previous face/edge); only Shift+D is new.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
const { onKeyDown } = await import("@/components/viewer/cellbuilder/keyboard");

const st = () => useCellBuilderStore.getState();
const cellId = (name: string) => Object.values(st().cells).find((c) => c.name === name)!.id;

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "Space_01", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3 }],
  equipments: [{ NAME: "Equipment_01", SPACE_NAME: "Space_01", GLOBAL_COORDS: true, X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1 }],
} as unknown as ProceduralDoc;

function withModel(run: () => void): void {
  st().open("m", "Model", 1, doc);
  try {
    run();
  } finally {
    st().close();
  }
}

const key = (k: string, shift = true) =>
  ({ key: shift ? k.toUpperCase() : k, code: `Key${k.toUpperCase()}`, shiftKey: shift, ctrlKey: false, metaKey: false, altKey: false, target: null, preventDefault() {}, stopPropagation() {} }) as unknown as KeyboardEvent;

test("duplicating a space copies it in place, under the next free name, and arms the move gizmo", () => {
  withModel(() => {
    const s1 = cellId("Space_01");
    st().selectItems([s1], "only");
    const before = Object.keys(st().cells).length;
    const ids = st().duplicateSelection();
    assert.ok(ids && ids.length === 1);
    assert.equal(Object.keys(st().cells).length, before + 1);
    const copy = st().cells[ids![0]];
    assert.equal(copy.name, "Space_02");
    assert.deepEqual(copy.origin, st().cells[s1].origin, "placed on top of the original");
    assert.deepEqual(st().selectedCellIds, [ids![0]]);
    assert.equal(st().gizmoMode, "translate");
  });
});

test("duplicating a space does NOT copy its equipment unless the equipment is itself selected (Q7)", () => {
  withModel(() => {
    const s1 = cellId("Space_01");
    const equipCountBefore = Object.values(st().cells).filter((c) => c.kind === "equipment").length;
    st().selectItems([s1], "only");
    st().duplicateSelection();
    const equipCountAfter = Object.values(st().cells).filter((c) => c.kind === "equipment").length;
    assert.equal(equipCountAfter, equipCountBefore, "no extra equipment appeared");
  });
});

test("duplicating two spaces and a selected equipment: three copies, one undo step", () => {
  withModel(() => {
    const s1 = cellId("Space_01");
    const e1 = cellId("Equipment_01");
    st().addCell("cell", [10, 0, 0], [4, 4, 3]); // Space_02
    const s2 = cellId("Space_02");
    st().selectItems([s1, s2, e1], "only");
    const past = st().past.length;
    const ids = st().duplicateSelection();
    assert.equal(ids?.length, 3);
    assert.equal(st().past.length, past + 1, "one undo step for the whole duplicate");
    const names = ids!.map((id) => st().cells[id].name).sort();
    assert.deepEqual(names, ["Equipment_02", "Space_03", "Space_04"].sort());
    st().undo();
    assert.equal(Object.keys(st().cells).length, 3, "back to the three originals");
  });
});

test("duplicateSelection returns null when nothing is selected", () => {
  withModel(() => {
    st().setSelection(null);
    assert.equal(st().duplicateSelection(), null);
  });
});

test("a stub ref provider's duplicate is called once with all its selected refs", () => {
  withModel(() => {
    let calls = 0;
    let gotKeys: string[] = [];
    const off = registerBuilderRefs({
      owner: "dup-test",
      project: () => [
        { key: "a", name: "a", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] },
        { key: "b", name: "b", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] },
      ],
      write: () => true,
      duplicate: (refs) => {
        calls += 1;
        gotKeys = refs.map((r) => r.key).sort();
        return refs.map((r) => `${r.key}-copy`);
      },
    });
    try {
      st().selectItems(["dup-test:a", "dup-test:b"], "only");
      const ids = st().duplicateSelection();
      assert.equal(calls, 1, "one call for the whole batch, not one per ref");
      assert.deepEqual(gotKeys, ["a", "b"]);
      assert.deepEqual(ids?.sort(), ["dup-test:a-copy", "dup-test:b-copy"]);
      assert.deepEqual(st().selectedRefIds.sort(), ["dup-test:a-copy", "dup-test:b-copy"]);
    } finally {
      off();
    }
  });
});

test("a provider with no duplicate verb is simply skipped", () => {
  withModel(() => {
    const off = registerBuilderRefs({
      owner: "no-dup-test",
      project: () => [{ key: "a", name: "a", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] }],
      write: () => true,
    });
    try {
      st().selectItems(["no-dup-test:a"], "only");
      assert.equal(st().duplicateSelection(), null);
    } finally {
      off();
    }
  });
});

test("nothing actually copyable opens no transaction -- no empty undo step, redo untouched", () => {
  withModel(() => {
    // Seed something to redo first, so a wrongly-opened transaction clearing `future` would show.
    st().addCell("cell", [20, 0, 0], [1, 1, 1]);
    st().undo();
    const future = st().future.length;
    assert.ok(future > 0, "sanity: there is something to redo");
    const past = st().past.length;

    const off = registerBuilderRefs({
      owner: "no-dup-plugin",
      project: () => [{ key: "a", name: "a", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] }],
      write: () => true,
      // No `duplicate` verb: nothing this selection names can actually be copied.
    });
    try {
      st().selectItems(["no-dup-plugin:a"], "only");
      assert.equal(st().duplicateSelection(), null);
      assert.equal(st().past.length, past, "beginTransaction was never called: no undo step for nothing");
      assert.equal(st().future.length, future, "and so redo was not cleared either");
    } finally {
      off();
    }
  });
});

test("a selection naming only an unknown cell id copies nothing and opens no transaction", () => {
  withModel(() => {
    const past = st().past.length;
    // A stale id (e.g. left over from a dropped `set` elsewhere) that resolves to no cell --
    // `cellIds` is non-empty, so this exercises the `hasCell` check itself, not the earlier
    // "cellIds.length === 0" return.
    useCellBuilderStore.setState({ selectedCellIds: ["does-not-exist"], selection: null } as never);
    assert.equal(st().duplicateSelection(), null);
    assert.equal(st().past.length, past, "no undo step for a selection with nothing left to copy");
  });
});

test("duplicating a loft bay copies the WHOLE member once, under the next free name", () => {
  withModel(() => {
    st().addLoftMember(); // seeds LOFT_01 with 3 stations (2 bays), selects its bay0
    const bay0 = st().cells[st().selection!.cellId!]!;
    assert.equal(bay0.kind, "loft");
    const membersBefore = st().loftMembers.length;
    const ids = st().duplicateSelection();
    assert.ok(ids);
    assert.equal(st().loftMembers.length, membersBefore + 1);
    const newMember = st().loftMembers.find((m) => m.NAME === "LOFT_02");
    assert.ok(newMember, "named off LOFT_01's own stem");
    assert.equal(ids!.length, newMember!.STATIONS.length - 1, "every bay of the new member, once");
    assert.deepEqual(st().selectedCellIds.sort(), ids!.sort());
  });
});

test("Shift+D wired through the keyboard handler duplicates the selection", () => {
  withModel(() => {
    const s1 = cellId("Space_01");
    st().selectItems([s1], "only");
    const before = Object.keys(st().cells).length;
    onKeyDown({} as never, key("d"));
    assert.equal(Object.keys(st().cells).length, before + 1);
  });
});
