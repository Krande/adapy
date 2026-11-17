import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

// Store-level: the Model panel's INCLUDE checkboxes are document edits. A
// component's INCLUDE (the workbook column) rides in its params, round-trips
// loadFromDoc -> toDoc, and toggling it -- one item, a space with everything in it,
// a structure -- is an undoable edit that marks the model dirty, exactly like any
// other Builder edit, so the next commit is a new revision carrying the flags.
//
// The store reads sessionStorage/localStorage at import and the commit path reads
// `window`: the panel realm provides both.
await import("../fixtures/panelRealm");

const doc: ProceduralDoc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [
    { NAME: "deckA", INCLUDE: true, X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 4, STRUCTURE_NAME: "Topside" },
    { NAME: "deckB", INCLUDE: true, X: 10, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 4, STRUCTURE_NAME: "Topside" },
    { NAME: "leg", INCLUDE: true, X: 0, Y: 0, Z: -30, DX: 4, DY: 4, DZ: 30, STRUCTURE_NAME: "Jacket" },
  ],
  equipments: [
    { NAME: "pump", INCLUDE: true, SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1, COGx: 0, COGy: 0, COGz: 0.5, massDry: 1, massCont: 0 },
    { NAME: "tank", INCLUDE: false, SPACE_NAME: "deckA", SPACE_LOC: "FLOOR", X: 5, Y: 5, Z: 0, LX: 2, LY: 2, LZ: 2, COGx: 0, COGy: 0, COGz: 1, massDry: 1, massCont: 0 },
    { NAME: "crane", INCLUDE: true, SPACE_NAME: "deckB", SPACE_LOC: "ROOF", X: 1, Y: 1, Z: 0, LX: 1, LY: 1, LZ: 1, COGx: 0, COGy: 0, COGz: 0.5, massDry: 1, massCont: 0 },
  ],
  openings: [
    { NAME: "door", INCLUDE: true, SPACE_NAME: "deckA", SUBTYPE: "door", USE_GLOBAL_COORDS: true, X: 2, Y: 0, Z: 0, DX: 1, DY: 0.2, DZ: 2 },
  ],
  groups: [
    { name: "Topside", blueprint: "steel_stru" },
    { name: "Jacket", blueprint: "jacket" },
  ],
};

const store = async () => (await import("@/state/cellBuilderStore")).useCellBuilderStore;
const tree = async () => import("@/utils/cellbuilder/modelTree");
const incl = async () => import("@/utils/cellbuilder/include");

type Store = Awaited<ReturnType<typeof store>>;
const byName = (s: Store, name: string) => {
  const c = Object.values(s.getState().cells).find((x) => x.name === name);
  assert.ok(c, `component ${name}`);
  return c;
};
/** The INCLUDE each entity of the written doc carries, by name. */
const written = (s: Store): Record<string, unknown> => {
  const out = s.getState().toDoc();
  const all = [...(out.spaces ?? []), ...(out.equipments ?? []), ...(out.openings ?? [])];
  return Object.fromEntries(all.map((e) => [String(e.NAME), e.INCLUDE]));
};

test("INCLUDE round-trips loadFromDoc -> toDoc; absent reads as included and is written true", async () => {
  const s = await store();
  s.getState().loadFromDoc(doc);
  assert.deepEqual(written(s), { deckA: true, deckB: true, leg: true, pump: true, tank: false, crane: true, door: true });
  const { isItemIncluded } = await incl();
  assert.equal(isItemIncluded(byName(s, "tank")), false);
  assert.equal(isItemIncluded(byName(s, "pump")), true);

  // A doc without the key at all (older docs, UI-placed components) is all included.
  s.getState().loadFromDoc({ ...doc, equipments: doc.equipments.map(({ INCLUDE: _i, ...e }) => e) });
  assert.equal(written(s).tank, true);
  assert.equal(s.getState().dirty, false, "loading is not an edit");
});

test("a doc before schema 1.1 builds everything: its INCLUDE false is the old default, not a choice", async () => {
  // Every doc stored before 1.1 carried INCLUDE false on items nobody excluded (the entity models'
  // default, dumped by the doc normalizer, read by no build). Unstamped or 1.0, it loads all included,
  // and the next save writes the 1.1 stamp without the stale flags.
  const s = await store();
  for (const stamp of [{ schema_version: undefined }, { schema_version: "1.0" }]) {
    s.getState().loadFromDoc({ ...doc, ...stamp });
    assert.equal(Object.values(written(s)).every(Boolean), true, JSON.stringify(written(s)));
  }
  assert.equal(s.getState().toDoc().schema_version, "1.1");
  s.getState().loadFromDoc(doc);
  assert.equal(written(s).tank, false, "a 1.1 doc's false is a choice");
});

test("one item: setCellsIncluded is an undoable, dirtying document edit", async () => {
  const s = await store();
  const st = () => s.getState();
  st().loadFromDoc(doc);
  assert.equal(st().dirty, false);
  const pastBefore = st().past.length;

  st().setCellsIncluded([byName(s, "pump").id], false);
  assert.equal(st().dirty, true);
  assert.equal(written(s).pump, false);
  assert.equal(st().past.length, pastBefore + 1, "one undo step");

  st().setCellsIncluded([byName(s, "tank").id], true);
  assert.equal(written(s).tank, true);
  assert.equal("INCLUDE" in byName(s, "tank").params, false, "included = no key in params, the writer defaults it");

  st().undo();
  assert.equal(written(s).tank, false);
  st().undo();
  assert.equal(written(s).pump, true);
  assert.equal(st().dirty, false, "undone back to the saved document");

  // No-ops: already so, unknown id, a loft band.
  const cellsBefore = st().cells;
  st().setCellsIncluded([byName(s, "pump").id], true);
  st().setCellsIncluded(["nope"], false);
  assert.equal(st().cells, cellsBefore);
  assert.equal(st().dirty, false);
});

test("a space cascades: the space and everything placed in it, one undo step; mixed when they differ", async () => {
  const s = await store();
  const st = () => s.getState();
  st().loadFromDoc(doc);
  const { buildModelTree } = await tree();
  const deckA = () => {
    const nodes = buildModelTree("tree", { cells: st().cells, groups: st().groups });
    const topside = nodes.find((n) => n.type === "group" && n.label === "Topside");
    assert.ok(topside && topside.type === "group");
    const row = topside.children.find((n) => n.type === "group" && n.label === "deckA");
    assert.ok(row && row.type === "group");
    return row;
  };
  assert.equal(deckA().include, "mixed", "the tank is out");
  assert.deepEqual(deckA().itemIds.map((id) => st().cells[id].name), ["deckA", "pump", "tank", "door"]);

  const past = st().past.length;
  // Ticking a mixed box includes everything under it.
  st().setCellsIncluded(deckA().itemIds, true);
  assert.equal(deckA().include, "on");
  assert.equal(written(s).tank, true);
  assert.equal(st().past.length, past + 1);

  st().setCellsIncluded(deckA().itemIds, false);
  assert.equal(deckA().include, "off");
  const w = written(s);
  assert.deepEqual([w.deckA, w.pump, w.tank, w.door], [false, false, false, false]);
  assert.deepEqual([w.deckB, w.crane, w.leg], [true, true, true], "the other space is untouched");

  st().undo();
  assert.equal(deckA().include, "on");
  st().undo();
  assert.equal(deckA().include, "mixed");
  assert.equal(st().dirty, false);
});

test("a structure: its own INCLUDE (the Structures sheet); its components read as excluded with it, their flags untouched", async () => {
  const s = await store();
  const st = () => s.getState();
  st().loadFromDoc(doc);
  const { itemIncludeState } = await incl();
  const { buildModelTree } = await tree();
  const state = (name: string) => itemIncludeState(byName(s, name), st().cells, st().groups);

  st().setGroupIncluded("Jacket", false);
  assert.equal(st().dirty, true);
  assert.deepEqual(st().toDoc().groups, [
    { name: "Topside", blueprint: "steel_stru" },
    { name: "Jacket", blueprint: "jacket", include: false },
  ]);
  assert.equal(state("leg"), "excluded-by-structure");
  assert.equal(written(s).leg, true, "the space's own flag is not rewritten");
  assert.equal(state("deckA"), "included");
  assert.equal(state("tank"), "excluded", "its own flag is reported first");

  const nodes = buildModelTree("tree", { cells: st().cells, groups: st().groups });
  const jacket = nodes.find((n) => n.type === "group" && n.label === "Jacket");
  assert.ok(jacket && jacket.type === "group");
  assert.equal(jacket.include, "off");
  assert.ok(jacket.children.every((n) => n.state === "excluded-by-structure"));

  st().setGroupIncluded("Jacket", true);
  assert.equal(state("leg"), "included");
});

test("the flags ride on the committed revision: a commit writes the doc with them and bumps the revision", async () => {
  const s = await store();
  const st = () => s.getState();
  const { capabilities } = await import("@/services/capabilities");
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const original = proc.commitModel;
  const calls: { modelId: string; doc: ProceduralDoc; revision: number }[] = [];
  proc.commitModel = async (_scope: unknown, modelId: string, d: ProceduralDoc, revision: number) => {
    calls.push({ modelId, doc: d, revision });
    return { revision: revision + 1 };
  };
  try {
    st().open("m-1", "Model", 3, doc);
    st().setCellsIncluded([byName(s, "crane").id], false);
    st().setGroupIncluded("Jacket", false);
    assert.equal(st().dirty, true);
    const ok = await st().commit();
    assert.equal(ok, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].revision, 3);
    const crane = (calls[0].doc.equipments ?? []).find((e) => e.NAME === "crane");
    assert.equal(crane?.INCLUDE, false);
    assert.equal(crane?.SPACE_NAME, "deckB", "the rest of the entity is as it was");
    assert.deepEqual((calls[0].doc.groups ?? []).map((x) => x.include), [undefined, false]);
    assert.equal(st().active?.revision, 4);
    assert.equal(st().dirty, false);
  } finally {
    proc.commitModel = original;
    st().close?.();
  }
});

test("selectCells selects a group row's components together, the first as primary", async () => {
  const s = await store();
  const st = () => s.getState();
  st().loadFromDoc(doc);
  const ids = [byName(s, "deckA").id, byName(s, "pump").id, "nope", byName(s, "pump").id];
  st().selectCells(ids);
  assert.deepEqual(st().selectedCellIds, [ids[0], ids[1]]);
  assert.deepEqual(st().selection, { kind: "cell", cellId: ids[0] });
  st().selectCells([]);
  assert.equal(st().selection, null);
  assert.deepEqual(st().selectedCellIds, []);
});
