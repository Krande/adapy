// The builder's info label (I): what it says of each kind of pick, that it follows the pick and
// goes with the flag or the cells, and that the keyboard equipment insert moved to Q.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const THREE = await import("three");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { infoLines, syncInfoLabel, disposeInfoLabel } = await import("@/components/viewer/cellbuilder/infoLabel");
const { onKeyDown } = await import("@/components/viewer/cellbuilder/keyboard");

type Cell = import("@/state/cellBuilderStore").BuilderCell;
type Ref = import("@/state/cellbuilder/refsSlice").BuilderRef;

const space: Cell = { id: "s1", name: "S1", kind: "cell", origin: [0, 0, 0], size: [6.875, 4, 3], params: {} };
const pump: Cell = {
  id: "e1",
  name: "P-1",
  kind: "equipment",
  equipmentType: "Pump",
  origin: [1, 1, 0],
  size: [2, 1, 1.5],
  params: { massDry: 1200, massCont: 300, COGx: 1, COGy: 0.5, COGz: 0.75 },
};
const door: Cell = { id: "o1", name: "D1", kind: "opening", subtype: "door", openingFrame: "local", origin: [0, 1, 0], size: [0.2, 1, 2.1], params: { SPACE_SIDE: "-X" } };
const hole: Cell = { id: "o2", name: "H1", kind: "opening", origin: [0, 0, 2.9], size: [1, 2, 0.2], params: {} };
const ref = (info?: string[]): Ref => ({
  id: "p:k",
  owner: "p",
  key: "k",
  name: "M1",
  kindLabel: "Modification",
  origin: [0, 0, 0],
  size: [1, 2, 3],
  include: true,
  look: "box",
  edit: { move: false, resize: false, rename: false, remove: false },
  ...(info ? { info } : {}),
});
const state = (refInfo?: string[]) => ({ cells: { s1: space, e1: pump, o1: door, o2: hole }, refs: { "p:k": ref(refInfo) } });

test("a space shows its three dimensions, as the resize readout writes them", () => {
  assert.deepEqual(infoLines({ kind: "cell", cellId: "s1" }, state()), ["Space S1", "X 6.875m", "Y 4m", "Z 3m"]);
  assert.deepEqual(infoLines({ kind: "face", cellId: "s1", faceIndex: 2 }, state()), ["Space S1", "X 6.875m", "Y 4m", "Z 3m"], "a face of it too");
});

test("an equipment shows its size, masses and centre of gravity, each only when its params carry it", () => {
  assert.deepEqual(infoLines({ kind: "cell", cellId: "e1" }, state()), [
    "Pump P-1",
    "L×W×H 2m × 1m × 1.5m",
    "Mass 1 200 kg dry + 300 kg contents",
    "COG (1m, 0.5m, 0.75m)",
  ]);
  const bare = { ...state(), cells: { e1: { ...pump, params: {} } } };
  assert.deepEqual(infoLines({ kind: "cell", cellId: "e1" }, bare), ["Pump P-1", "L×W×H 2m × 1m × 1.5m"]);
  const dryOnly = { ...state(), cells: { e1: { ...pump, params: { massDry: "500" } } } };
  assert.deepEqual(infoLines({ kind: "cell", cellId: "e1" }, dryOnly).slice(2), ["Mass 500 kg dry"]);
});

test("an opening shows its width and height in its side's plane, its depth and its side", () => {
  assert.deepEqual(infoLines({ kind: "cell", cellId: "o1" }, state()), ["Door D1", "W×H 1m × 2.1m", "Depth 0.2m", "Side -X"]);
  // A global box names no side: its thinnest axis is the depth.
  assert.deepEqual(infoLines({ kind: "cell", cellId: "o2" }, state()), ["Opening H1", "W×H 1m × 2m", "Depth 0.2m"]);
});

test("a ref shows what its provider says of it, else its size; nothing picked, nothing shown", () => {
  assert.deepEqual(infoLines({ kind: "ref", refId: "p:k" }, state(["Kind: strengthening", "Plate 12 mm"])), [
    "Modification M1",
    "Kind: strengthening",
    "Plate 12 mm",
  ]);
  assert.deepEqual(infoLines({ kind: "ref", refId: "p:k" }, state()), ["Modification M1", "1m × 2m × 3m"]);
  assert.deepEqual(infoLines(null, state()), []);
  assert.deepEqual(infoLines({ kind: "cell", cellId: "gone" }, state()), []);
});

test("the label follows the pick, and hides with the flag, the cells or the pick", () => {
  const ctx = { container: new THREE.Group(), infoLabel: null as InstanceType<typeof THREE.Sprite> | null };
  const base = state();
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: base.cells,
    refs: base.refs,
    selection: { kind: "cell", cellId: "s1" },
    infoLabel: true,
    cellsVisible: true,
  } as never);
  try {
    syncInfoLabel(ctx as never);
    const sprite = ctx.infoLabel!;
    assert.ok(sprite && sprite.visible, "shown");
    assert.equal(sprite.parent, ctx.container, "in the builder's layer: hidden with it in a mode that hides the cells");
    assert.deepEqual(sprite.position.toArray(), [3.4375, 2, 3], "above the middle of the box's top");
    assert.equal(sprite.userData.text, "Space S1\nX 6.875m\nY 4m\nZ 3m");

    useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "e1" } } as never);
    syncInfoLabel(ctx as never);
    assert.deepEqual(sprite.position.toArray(), [2, 1.5, 1.5], "moved with the pick");
    assert.match(sprite.userData.text, /^Pump P-1/);

    useCellBuilderStore.setState({ cellsVisible: false } as never);
    syncInfoLabel(ctx as never);
    assert.equal(sprite.visible, false, "cells switched off");
    useCellBuilderStore.setState({ cellsVisible: true, infoLabel: false } as never);
    syncInfoLabel(ctx as never);
    assert.equal(sprite.visible, false, "flag off");
    useCellBuilderStore.setState({ infoLabel: true, selection: null } as never);
    syncInfoLabel(ctx as never);
    assert.equal(sprite.visible, false, "nothing picked");
    disposeInfoLabel(ctx as never);
    assert.equal(ctx.infoLabel, null);
    assert.equal(ctx.container.children.length, 0);
  } finally {
    useCellBuilderStore.setState({ active: null, cells: {}, refs: {}, selection: null, infoLabel: false } as never);
  }
});

const press = (key: string) => {
  let taken = false;
  const ev = { key, code: `Key${key.toUpperCase()}`, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, target: null, preventDefault: () => void (taken = true), stopPropagation: () => {} };
  return { ev: ev as unknown as KeyboardEvent, taken: () => taken };
};

test("I toggles the info label; Q starts the keyboard equipment insert, which reads 'Insert <type> in <host>'", async () => {
  const { endEquipEntry } = await import("@/components/viewer/cellbuilder/insertTools");
  const readout: string[] = [];
  const canvas = { getContext: () => new Proxy({}, { get: (_t, p) => (p === "fillText" ? (t: string) => void readout.push(t) : () => {}), set: () => true }) };
  const ctx = {
    numEntry: null, placeEntry: null, equipEntry: null as unknown, openEntry: null, modalMove: null, loftActive: null,
    ghost: new THREE.Mesh(), ghostBox: null, container: new THREE.Group(), placeFrame: new THREE.Group(),
    readoutCanvas: canvas, readoutTex: { needsUpdate: false }, readout: new THREE.Sprite(),
  };
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: { s1: space },
    refs: {},
    selection: null,
    infoLabel: false,
    equipmentTypes: [{ slug: "pump", name: "Pump" }],
    selectedEquipmentType: "pump",
  } as never);
  try {
    const i = press("i");
    onKeyDown(ctx as never, i.ev);
    assert.equal(i.taken(), true);
    assert.equal(useCellBuilderStore.getState().infoLabel, true);
    assert.equal(ctx.equipEntry, null, "I no longer starts an insert");
    onKeyDown(ctx as never, press("i").ev);
    assert.equal(useCellBuilderStore.getState().infoLabel, false, "again: off");

    const q = press("q");
    onKeyDown(ctx as never, q.ev);
    assert.equal(q.taken(), true);
    assert.ok(ctx.equipEntry, "Q starts the insert");
    assert.equal(readout.at(-1), "Insert Pump in S1");
    endEquipEntry(ctx as never);
  } finally {
    useCellBuilderStore.setState({ active: null, cells: {}, refs: {}, selection: null, infoLabel: false } as never);
  }
});

test("one label on screen, the current pick's: a new pick draws a new texture, and another scene's label goes", () => {
  const ctxA = { container: new THREE.Group(), infoLabel: null as InstanceType<typeof THREE.Sprite> | null };
  const ctxB = { container: new THREE.Group(), infoLabel: null as InstanceType<typeof THREE.Sprite> | null };
  const base = state(["Kind: strengthening"]);
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: base.cells,
    refs: base.refs,
    selection: { kind: "cell", cellId: "e1" },
    infoLabel: true,
    cellsVisible: true,
  } as never);
  try {
    syncInfoLabel(ctxA as never);
    const sprite = ctxA.infoLabel!;
    const first = (sprite.material as InstanceType<typeof THREE.SpriteMaterial>).map!;
    let disposed = false;
    first.addEventListener("dispose", () => void (disposed = true));
    useCellBuilderStore.setState({ selection: { kind: "cell", cellId: "o1" } } as never);
    syncInfoLabel(ctxA as never);
    const second = (sprite.material as InstanceType<typeof THREE.SpriteMaterial>).map!;
    assert.notEqual(second.uuid, first.uuid, "a texture of its own: nothing of the equipment's label is left in it");
    assert.equal(disposed, true, "the previous pick's texture is let go");
    assert.match(sprite.userData.text, /^Door D1/);
    assert.doesNotMatch(sprite.userData.text, /Pump/, "the equipment's lines are not in it");
    assert.equal(ctxA.container.children.filter((c) => (c as { isSprite?: boolean }).isSprite).length, 1, "one sprite");
    // A second builder scene showing its label: the first one's goes.
    syncInfoLabel(ctxB as never);
    assert.equal(ctxB.infoLabel!.visible, true);
    assert.equal(sprite.visible, false);
    disposeInfoLabel(ctxA as never);
    disposeInfoLabel(ctxB as never);
  } finally {
    useCellBuilderStore.setState({ active: null, cells: {}, refs: {}, selection: null, infoLabel: false } as never);
  }
});
