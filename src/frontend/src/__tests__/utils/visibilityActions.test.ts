// Hide selected, Hide others, Show all and Show selected act on the builder's selected cells while its
// cells are drawn, else on the viewport -- so the keys a profile gives them (GeniE: Alt+Minus, Alt+S,
// Alt+A, Alt+Plus) work in Topology > Input as in Output. Show selected brings back what is hidden under
// the tree's selected rows: select a level and its hidden elements come back.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { setBuilderVisibilityHost } = await import("@/state/cellbuilder/builderVisibility");
const { registerBuilderRefs } = await import("@/plugins/builderRefs");
/** The builder's host as initScene lends it, its cells drawn or not — mirrors
 *  components/viewer/cellbuilder/initScene.ts's host wiring, refs included, with the mixed
 *  multi-select set too: a lone ref pick has no cellId, and `all()` must offer refs too, or
 *  Hide others / Unhide all would leave them out. */
const lend = (drawn: boolean) =>
  setBuilderVisibilityHost({
    cellsShown: () => drawn && useCellBuilderStore.getState().active !== null,
    selected: () => {
      const st = useCellBuilderStore.getState();
      const ids = [...st.selectedCellIds, ...st.selectedRefIds];
      if (ids.length) return ids;
      if (st.selection?.cellId) return [st.selection.cellId];
      if (st.selection?.kind === "ref") return [st.selection.refId];
      return [];
    },
    all: () => {
      const st = useCellBuilderStore.getState();
      return [...Object.keys(st.cells), ...Object.keys(st.refs)];
    },
    hide: (ids) => useCellBuilderStore.getState().hideCells([...ids]),
    unhide: (ids) => useCellBuilderStore.getState().unhideCells([...ids]),
    unhideAll: () => useCellBuilderStore.getState().unhideAllCells(),
    // Show complement swaps `hiddenCellIds`/`hiddenRefIds` for their complement among `all()`.
    showComplement: () => {
      const st = useCellBuilderStore.getState();
      const keepCells = new Set(st.hiddenCellIds);
      const keepRefs = new Set(st.hiddenRefIds);
      useCellBuilderStore.setState({
        hiddenCellIds: Object.keys(st.cells).filter((id) => !keepCells.has(id)),
        hiddenRefIds: Object.keys(st.refs).filter((id) => !keepRefs.has(id)),
        selectedCellIds: [],
        selectedRefIds: [],
        selection: null,
      } as never);
    },
    anyHidden: () => {
      const st = useCellBuilderStore.getState();
      return st.hiddenCellIds.length > 0 || st.hiddenRefIds.length > 0;
    },
  });
const {
  hasHiddenToComplement,
  hasShowTarget,
  hasVisibilitySelection,
  hideOthers,
  hideSelection,
  showAll,
  showComplement,
  showSelection,
} = await import("@/utils/scene/visibilityActions");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { matchShortcut, parseShortcut } = await import("@/layout/shortcuts");
const { chordFromEvent } = await import("@/layout/keyBindings");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type ObjectRef = import("@/viewport/api/viewportApi").ObjectRef;

const before = useCellBuilderStore.getState();
// `refs` is projected (never set directly): the store's own subscription re-projects it from
// `providers` whenever `cells`/`docExtras` changes (state/cellbuilder/store.ts), which would
// clobber a hand-set `refs` field the moment openBuilder's setState below touches `cells`. A real
// provider, registered per test, is the only way a ref reliably survives that.
function openBuilder(selected: string[], refSelection?: string) {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "n", revision: 1 },
    cells: { A: { id: "A" }, B: { id: "B" }, C: { id: "C" } },
    selectedCellIds: selected,
    selection: refSelection
      ? { kind: "ref", refId: refSelection }
      : selected.length
        ? { kind: "cell", cellId: selected[0] }
        : null,
    hiddenCellIds: [],
    hiddenRefIds: [],
  } as never);
}
function closeBuilder() {
  useCellBuilderStore.setState({
    active: before.active, cells: before.cells, selectedCellIds: before.selectedCellIds,
    selection: before.selection, hiddenCellIds: before.hiddenCellIds, hiddenRefIds: before.hiddenRefIds,
  } as never);
}
const hidden = () => [...useCellBuilderStore.getState().hiddenCellIds].sort();
const hiddenRefs = () => [...useCellBuilderStore.getState().hiddenRefIds].sort();

function viewport(selectedInit: ObjectRef[] = []) {
  const calls: string[] = [];
  const shown: string[] = [];
  let selected = selectedInit;
  let hiddenNow = false;
  const api = {
    kind: "three",
    hideSelected: () => {
      calls.push("hideSelected");
      hiddenNow = selected.length > 0;
      selected = []; // hiding clears the selection, as the real viewports do
    },
    hideOthers: () => void calls.push("hideOthers"),
    showAll: () => {
      calls.push("showAll");
      hiddenNow = false;
    },
    showComplement: () => {
      calls.push("showComplement");
      hiddenNow = !hiddenNow;
    },
    hasHidden: () => hiddenNow,
    show: (refs: Iterable<ObjectRef>) => void shown.push(...[...refs].map((r) => r.id)),
    selection: () => selected,
    setSelection: (refs: Iterable<ObjectRef>) => void (selected = [...refs]),
  } as unknown as ViewportApi;
  return { api, calls, shown };
}

test("with the builder's cells drawn and some selected, hide and show act on those cells", () => {
  const unprobe = lend(true);
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  try {
    openBuilder(["A", "B"]);
    assert.ok(hasVisibilitySelection());
    assert.ok(hideSelection());
    assert.deepEqual(hidden(), ["A", "B"]);
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, [], "a hidden cell is no longer selected");
    // Picked again in the Model panel (its rows list hidden cells too), they come back.
    useCellBuilderStore.getState().selectCells(["A", "B"]);
    assert.ok(showSelection());
    assert.deepEqual(hidden(), []);
    assert.ok(hideOthers());
    assert.deepEqual(hidden(), ["C"]);
    assert.ok(showAll());
    assert.deepEqual(hidden(), []);
    assert.deepEqual(v.calls, ["showAll"], "Show all shows the scene too; the rest left it alone");
  } finally {
    withdraw();
    unprobe();
    closeBuilder();
  }
});

test("a lone ref pick hides and shows through the same global actions as a cell", () => {
  const unprobe = lend(true);
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const offRef = registerBuilderRefs({
    owner: "test",
    project: () => [{ key: "R1", name: "R1", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] }],
  });
  const REF = "test:R1"; // builderRefId(owner, key)
  try {
    openBuilder([], REF); // a ref pick, no cell selected: selectedCellIds stays empty
    assert.ok(useCellBuilderStore.getState().refs[REF], "the provider's ref is projected");
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, []);
    assert.ok(hasVisibilitySelection(), "a ref pick counts as something to act on");
    assert.ok(hideSelection());
    assert.deepEqual(hiddenRefs(), [REF]);
    assert.deepEqual(hidden(), [], "it is filed under refs, not cells");
    assert.equal(useCellBuilderStore.getState().selection, null, "a hidden pick is no longer selected");
    useCellBuilderStore.setState({ selection: { kind: "ref", refId: REF } } as never);
    assert.ok(showSelection());
    assert.deepEqual(hiddenRefs(), []);
    // Hide others with a ref selected takes every OTHER cell and ref (all() lists both kinds).
    assert.ok(hideOthers());
    assert.deepEqual(hidden().sort(), ["A", "B", "C"]);
    assert.deepEqual(hiddenRefs(), [], "the selected ref itself is not an \"other\"");
    assert.ok(showAll());
    assert.deepEqual(hidden(), []);
    assert.deepEqual(hiddenRefs(), [], "Unhide all clears both (unhideAllCells)");
  } finally {
    withdraw();
    unprobe();
    offRef();
    closeBuilder();
  }
});

test("with the builder's cells not drawn (another mode), they are the viewport's", () => {
  const unprobe = lend(false);
  const v = viewport([{ model: "m", id: "7" }]);
  const withdraw = setActiveViewport(v.api);
  try {
    openBuilder(["A"]);
    hideSelection();
    hideOthers();
    assert.deepEqual(v.calls, ["hideSelected", "hideOthers"]);
    assert.deepEqual(hidden(), [], "the hidden cells are left alone");
  } finally {
    withdraw();
    unprobe();
    closeBuilder();
  }
});

test("Show selected: a level picked in the tree brings back everything under it", () => {
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const tree = useTreeViewStore.getState().tree;
  const level = {
    id: "lvl", name: "deck", children: [
      { id: "b1", name: "b1", model_key: "m", rangeId: 1, node_name: "n", children: [] },
      { id: "b2", name: "b2", model_key: "m", rangeId: 2, node_name: "n", children: [] },
    ],
  };
  useTreeViewStore.setState({ tree: { selectedNodes: [{ data: level }] } } as never);
  try {
    assert.ok(showSelection());
    assert.deepEqual(v.shown.sort(), ["1", "2"]);
  } finally {
    withdraw();
    useTreeViewStore.setState({ tree } as never);
  }
});

test("Alt+Plus: the chord names the key, and \"+\" matches with or without Shift", () => {
  assert.equal(parseShortcut("Alt+Plus")?.key, "+");
  const list = [{ id: "core:show-selected", keys: "Alt+Plus", label: "Show selected" }] as never;
  for (const shiftKey of [false, true]) {
    const hit = matchShortcut({ key: "+", shiftKey, ctrlKey: false, metaKey: false, altKey: true }, list);
    assert.equal(hit?.id, "core:show-selected", `shift ${shiftKey}`);
  }
  assert.equal(chordFromEvent({ key: "+", ctrlKey: false, metaKey: false, altKey: true, shiftKey: false }), "Alt+Plus", "the dialog records it by name");
});

// --------------------------------------------------------------------------
// Alt+Minus then Alt+Plus restores what was just hidden
// --------------------------------------------------------------------------

test("viewport: Show selected with nothing selected restores the last hide, as one undo-worthy step", () => {
  const v = viewport([{ model: "m", id: "7" }]);
  const withdraw = setActiveViewport(v.api);
  try {
    assert.ok(hideSelection());
    assert.deepEqual(v.calls, ["hideSelected"]);
    assert.deepEqual([...v.api.selection()], [], "hiding cleared the selection, as it always does");
    assert.ok(showSelection(), "nothing selected now, but there is a memory of the last hide");
    assert.deepEqual(v.shown, ["7"], "exactly what was hidden comes back");
    // One-shot: a second press with still nothing selected has nothing left to restore.
    assert.equal(showSelection(), false);
  } finally {
    withdraw();
  }
});

test("builder: the same one-shot restore for the builder's cells", () => {
  const unprobe = lend(true);
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  try {
    openBuilder(["A", "B"]);
    assert.ok(hideSelection());
    assert.deepEqual(hidden(), ["A", "B"]);
    assert.deepEqual(useCellBuilderStore.getState().selectedCellIds, []);
    assert.ok(showSelection(), "nothing selected now; restores the memory of the last hide");
    assert.deepEqual(hidden(), []);
    assert.equal(showSelection(), false, "one-shot: nothing left to restore");
  } finally {
    withdraw();
    unprobe();
    closeBuilder();
  }
});

test("an explicit (non-empty) selection always wins over the memory of the last hide", () => {
  const v = viewport([{ model: "m", id: "7" }]);
  const withdraw = setActiveViewport(v.api);
  try {
    assert.ok(hideSelection()); // lastHidden = {kind: "viewport", refs: ["7"]}
    v.api.setSelection!([{ model: "m", id: "9" }]); // a NEW, different selection
    assert.ok(showSelection());
    assert.deepEqual(v.shown, ["9"], "the explicit selection, not the memory of \"7\"");
  } finally {
    withdraw();
  }
});

test("Hide others / Show all / Show complement each clear the memory of the last hide", () => {
  for (const clearer of ["hideOthers", "showAll", "showComplement"] as const) {
    const v = viewport([{ model: "m", id: "7" }]);
    const withdraw = setActiveViewport(v.api);
    try {
      assert.ok(hideSelection()); // something hidden, remembered
      if (clearer === "hideOthers") hideOthers();
      else if (clearer === "showAll") showAll();
      else showComplement();
      assert.equal(showSelection(), false, `${clearer} must have cleared the memory`);
    } finally {
      withdraw();
    }
  }
});

test("hasShowTarget covers both a selection and the memory of the last hide", () => {
  const v = viewport([{ model: "m", id: "7" }]);
  const withdraw = setActiveViewport(v.api);
  try {
    assert.equal(hasShowTarget(), true, "something is selected");
    hideSelection();
    assert.equal(hasShowTarget(), true, "nothing selected, but there is a memory to restore");
    showSelection();
    assert.equal(hasShowTarget(), false, "restored and forgotten; nothing left");
  } finally {
    withdraw();
  }
});

// --------------------------------------------------------------------------
// Show complement
// --------------------------------------------------------------------------

test("viewport: a double Show complement is the identity", () => {
  const v = viewport([{ model: "m", id: "7" }]);
  const withdraw = setActiveViewport(v.api);
  try {
    assert.equal(hasHiddenToComplement(), false);
    hideSelection();
    assert.equal(hasHiddenToComplement(), true);
    assert.ok(showComplement());
    assert.deepEqual(v.calls, ["hideSelected", "showComplement"]);
    assert.ok(showComplement());
    assert.deepEqual(v.calls, ["hideSelected", "showComplement", "showComplement"]);
    assert.equal(hasHiddenToComplement(), true, "two swaps land back where the one hide left it");
  } finally {
    withdraw();
  }
});

test("builder: hide all minus hidden, unhide hidden -- including refs", () => {
  const unprobe = lend(true);
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  const offRef = registerBuilderRefs({
    owner: "complement-test",
    project: () => [{ key: "R1", name: "R1", kindLabel: "Load", origin: [0, 0, 0], size: [1, 1, 1] }],
  });
  const REF = "complement-test:R1";
  try {
    openBuilder(["A"]);
    assert.ok(hideSelection()); // A hidden
    assert.deepEqual(hidden(), ["A"]);
    assert.ok(showComplement()); // swap: A shown, B/C/REF hidden
    assert.deepEqual(hidden().sort(), ["B", "C"]);
    assert.deepEqual(hiddenRefs(), [REF]);
    assert.ok(showComplement()); // swap back: identity
    assert.deepEqual(hidden(), ["A"]);
    assert.deepEqual(hiddenRefs(), []);
  } finally {
    withdraw();
    unprobe();
    offRef();
    closeBuilder();
  }
});

test("hasHiddenToComplement -- the \"Nothing is hidden\" disabled reason -- is false on either surface with nothing hidden", () => {
  // The command's disabledReason reads this, not showComplement() itself: showComplement() still
  // RUNS with nothing hidden (its complement is "hide everything"), as every other visibility
  // action runs regardless of whether there is anything selected/hidden to act on.
  const v = viewport();
  const withdraw = setActiveViewport(v.api);
  try {
    assert.equal(hasHiddenToComplement(), false);
  } finally {
    withdraw();
  }
  const unprobe = lend(true);
  try {
    openBuilder([]);
    assert.equal(hasHiddenToComplement(), false, "the builder's cells/refs are all shown");
  } finally {
    unprobe();
    closeBuilder();
  }
});
