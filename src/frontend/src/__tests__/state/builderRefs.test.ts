// Builder refs: a plugin's rows shown as boxes among the builder's cells. The refs are a
// projection of the doc's extras and the cells, never stored: an edit goes to the provider,
// which writes its own doc key, and the refs follow -- through undo and redo too.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
type Provider = import("@/state/cellbuilder/refsSlice").BuilderRefProvider;
type Vec3 = [number, number, number];

/** A plugin's own rows, as an engine keeps them in the doc. */
interface Marker {
  KEY: string;
  NAME: string;
  X: number;
  Y: number;
  Z: number;
  DX: number;
  DY: number;
  DZ: number;
  INCLUDE: boolean;
  STRUCTURE?: string;
  SPACE?: string;
}

const markers: Marker[] = [
  { KEY: "a", NAME: "marker_a", X: 0.03, Y: 0.07, Z: 0.11, DX: 2, DY: 1, DZ: 1, INCLUDE: true, STRUCTURE: "Upper", SPACE: "room" },
  { KEY: "b", NAME: "marker_b", X: 5, Y: 5, Z: 0, DX: 1, DY: 1, DZ: 0.02, INCLUDE: false, STRUCTURE: "*" },
];

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "room", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "Upper" }],
  equipments: [],
  groups: [{ name: "Upper", blueprint: "plate" }],
  markers,
} as unknown as ProceduralDoc;

const st = () => useCellBuilderStore.getState();
const rows = (): Marker[] => (st().docExtras.markers as Marker[] | undefined) ?? [];
const writeRows = (next: Marker[]) => st().setDocExtra("markers", next);
const patch = (key: string, p: Partial<Marker>) => writeRows(rows().map((m) => (m.KEY === key ? { ...m, ...p } : m)));

let projections = 0;
const provider: Provider = {
  owner: "test-markers",
  project(extras) {
    projections++;
    return ((extras.markers as Marker[] | undefined) ?? []).map((m) => ({
      key: m.KEY,
      name: m.NAME,
      kindLabel: "Marker",
      origin: [m.X, m.Y, m.Z] as Vec3,
      size: [m.DX, m.DY, m.DZ] as Vec3,
      structure: m.STRUCTURE,
      space: m.SPACE,
      include: m.INCLUDE,
      look: m.DZ < 0.05 ? ("face" as const) : ("box" as const),
      edit: { move: true, resize: true, rename: true, remove: true },
    }));
  },
  setIncluded: (ref, on) => patch(ref.key, { INCLUDE: on }),
  rename: (ref, name) => patch(ref.key, { NAME: name }),
  remove: (ref) => writeRows(rows().filter((m) => m.KEY !== ref.key)),
  write: (ref, box) => {
    patch(ref.key, { X: box.origin[0], Y: box.origin[1], Z: box.origin[2], DX: box.size[0], DY: box.size[1], DZ: box.size[2] });
    return true;
  },
};

const A = "test-markers:a";
const B = "test-markers:b";

function withProvider(run: () => void): void {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs(provider);
  try {
    run();
  } finally {
    off();
    st().close();
  }
}

test("the refs are projected from the doc, and again only when the extras or the cells change", () => {
  withProvider(() => {
    const refs = st().refs;
    assert.deepEqual(Object.keys(refs).sort(), [A, B]);
    assert.equal(refs[A].owner, "test-markers");
    assert.equal(refs[A].key, "a");
    assert.deepEqual(refs[A].origin, [0.03, 0.07, 0.11]);
    assert.equal(refs[B].include, false);
    assert.equal(refs[B].look, "face");
    const before = projections;
    st().setSelectMode("face");
    st().setGridStep(0.5);
    assert.equal(projections, before, "a change of neither projects nothing");
    assert.equal(st().refs, refs, "and leaves the same object");
    st().renameCell(Object.keys(st().cells)[0], "room_2");
    assert.equal(projections, before + 1, "a cell edit projects again");
  });
});

test("a provider's refs go with it, and a pick of a ref that went is dropped", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs(provider);
  st().setSelection({ kind: "ref", refId: A });
  off();
  assert.deepEqual(st().refs, {});
  assert.equal(st().selection, null);
  st().close();
});

test("a write is one undo step: undo and redo bring the ref back and forth through the doc", () => {
  withProvider(() => {
    const dirtyBefore = st().dirty;
    assert.equal(st().writeRef(A, { origin: [1, 2, 3], size: [2, 1, 1] }), true);
    assert.deepEqual(st().refs[A].origin, [1, 2, 3]);
    assert.equal(rows()[0].X, 1, "the provider wrote its own row");
    assert.equal(st().dirty, true);
    st().undo();
    assert.deepEqual(st().refs[A].origin, [0.03, 0.07, 0.11], "undone");
    assert.equal(st().dirty, dirtyBefore);
    st().redo();
    assert.deepEqual(st().refs[A].origin, [1, 2, 3], "redone");
  });
});

test("a burst of writes inside a transaction (a gizmo drag) is one undo step", () => {
  withProvider(() => {
    const past = st().past.length;
    st().beginTransaction();
    for (const x of [0.5, 1, 1.5]) st().writeRef(A, { origin: [x, 0.07, 0.11], size: [2, 1, 1] });
    st().endTransaction();
    assert.equal(st().past.length, past + 1);
    st().undo();
    assert.deepEqual(st().refs[A].origin, [0.03, 0.07, 0.11]);
  });
});

test("setIncluded, rename and remove go to the provider; the refs follow", () => {
  withProvider(() => {
    st().setRefIncluded(B, true);
    assert.equal(st().refs[B].include, true);
    st().renameRef(A, "  marker_renamed ");
    assert.equal(st().refs[A].name, "marker_renamed", "trimmed");
    st().renameRef(A, "   ");
    assert.equal(st().refs[A].name, "marker_renamed", "a blank name is refused");
    st().setSelection({ kind: "ref", refId: A });
    st().removeRef(A);
    assert.equal(st().refs[A], undefined);
    assert.equal(st().selection, null, "the pick of it went too");
    st().undo();
    assert.ok(st().refs[A], "a remove undoes");
  });
});

test("what the provider does not offer, or the ref's edit forbids, is not done", () => {
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs({
    owner: "read-only",
    project: () => [{ key: "x", name: "x", kindLabel: "X", origin: [0, 0, 0], size: [1, 1, 1], edit: { move: true, rename: true } }],
  });
  try {
    const ref = st().refs["read-only:x"];
    assert.deepEqual(ref.edit, { move: false, resize: false, rename: false, remove: false }, "no write, no rename: neither allowed");
    assert.equal(ref.include, true);
    assert.equal(ref.look, "box");
    assert.equal(st().writeRef(ref.id, { origin: [1, 1, 1], size: [1, 1, 1] }), false);
  } finally {
    off();
    st().close();
  }
});

test("selecting a ref: no cell is selected, the gizmo is off, and its structure becomes the active one", () => {
  withProvider(() => {
    st().setActiveGroup(null);
    const room = Object.keys(st().cells)[0];
    st().setSelection({ kind: "cell", cellId: room });
    st().setGizmoMode("translate");
    st().setSelection({ kind: "ref", refId: A });
    assert.deepEqual(st().selectedCellIds, []);
    assert.equal(st().gizmoMode, "none", "a new pick drops the gizmo");
    assert.equal(st().activeGroup, "Upper");
    st().setGizmoMode("translate");
    st().setSelection({ kind: "ref", refId: A });
    assert.equal(st().gizmoMode, "translate", "the same ref again keeps it");
    // An undo keeps a ref pick while the ref is still there.
    st().writeRef(A, { origin: [1, 0, 0], size: [2, 1, 1] });
    st().undo();
    assert.deepEqual(st().selection, { kind: "ref", refId: A });
  });
});

test("a provider that throws shows no refs and fails nothing else", () => {
  st().open("m", "Model", 1, doc);
  const warn = console.warn;
  console.warn = () => {};
  const offBad = registerBuilderRefs({ owner: "broken", project: () => { throw new Error("x"); } });
  const off = registerBuilderRefs(provider);
  try {
    assert.deepEqual(Object.keys(st().refs).sort(), [A, B]);
  } finally {
    off();
    offBad();
    console.warn = warn;
    st().close();
  }
});

test("renaming a space rewrites a provider's rows that name it, in the rename's own undo step", () => {
  const renaming: Provider = {
    ...provider,
    renameSpace: (from, to, extras) => {
      const list = (extras.markers as Marker[] | undefined) ?? [];
      return list.some((m) => m.SPACE === from) ? { markers: list.map((m) => (m.SPACE === from ? { ...m, SPACE: to } : m)), spaces: "not mine" } : null;
    },
  };
  st().open("m", "Model", 1, doc);
  const off = registerBuilderRefs(renaming);
  try {
    const room = Object.values(st().cells).find((c) => c.name === "room")!;
    const steps = st().past.length;
    st().renameCell(room.id, "hall");
    assert.equal(st().cells[room.id].name, "hall");
    assert.deepEqual(rows().map((m) => m.SPACE), ["hall", undefined], "the row naming the space follows it");
    assert.equal(st().refs[A].space, "hall");
    assert.equal(st().docExtras.spaces, undefined, "a key the builder owns is not written");
    assert.equal(st().past.length, steps + 1, "one undo step");
    st().undo();
    assert.equal(st().cells[room.id].name, "room");
    assert.deepEqual(rows().map((m) => m.SPACE), ["room", undefined], "one undo takes back both");
    const extras = st().docExtras;
    st().renameCell(room.id, "room");
    assert.equal(st().docExtras, extras, "no rename, no rewrite");
  } finally {
    off();
    st().close();
  }
});
