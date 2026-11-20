// Hide selected, Hide others, Show all and Show selected act on the builder's selected cells while its
// cells are drawn, else on the viewport -- so the keys a profile gives them (GeniE: Alt+Minus, Alt+S,
// Alt+A, Alt+Plus) work in Topology > Input as in Output. Show selected brings back what is hidden under
// the tree's selected rows: select a level and its hidden elements come back.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { setBuilderVisibilityHost } = await import("@/state/cellbuilder/builderVisibility");
/** The builder's host as initScene lends it, its cells drawn or not. */
const lend = (drawn: boolean) =>
  setBuilderVisibilityHost({
    cellsShown: () => drawn && useCellBuilderStore.getState().active !== null,
    selected: () => {
      const st = useCellBuilderStore.getState();
      return st.selectedCellIds.length ? [...st.selectedCellIds] : st.selection?.cellId ? [st.selection.cellId] : [];
    },
    all: () => Object.keys(useCellBuilderStore.getState().cells),
    hide: (ids) => useCellBuilderStore.getState().hideCells([...ids]),
    unhide: (ids) => useCellBuilderStore.getState().unhideCells([...ids]),
    unhideAll: () => useCellBuilderStore.getState().unhideAllCells(),
  });
const { hideOthers, hideSelection, showAll, showSelection, hasVisibilitySelection } = await import("@/utils/scene/visibilityActions");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { matchShortcut, parseShortcut } = await import("@/layout/shortcuts");
const { chordFromEvent } = await import("@/layout/keyBindings");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type ObjectRef = import("@/viewport/api/viewportApi").ObjectRef;

const before = useCellBuilderStore.getState();
function openBuilder(selected: string[]) {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "n", revision: 1 },
    cells: { A: { id: "A" }, B: { id: "B" }, C: { id: "C" } },
    selectedCellIds: selected,
    selection: selected.length ? { kind: "cell", cellId: selected[0] } : null,
    hiddenCellIds: [],
  } as never);
}
function closeBuilder() {
  useCellBuilderStore.setState({
    active: before.active, cells: before.cells, selectedCellIds: before.selectedCellIds,
    selection: before.selection, hiddenCellIds: before.hiddenCellIds,
  } as never);
}
const hidden = () => [...useCellBuilderStore.getState().hiddenCellIds].sort();

function viewport(selected: ObjectRef[] = []) {
  const calls: string[] = [];
  const shown: string[] = [];
  const api = {
    kind: "three",
    hideSelected: () => void calls.push("hideSelected"),
    hideOthers: () => void calls.push("hideOthers"),
    showAll: () => void calls.push("showAll"),
    show: (refs: Iterable<ObjectRef>) => void shown.push(...[...refs].map((r) => r.id)),
    selection: () => selected,
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
