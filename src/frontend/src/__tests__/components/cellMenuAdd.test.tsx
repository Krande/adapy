// The builder's cell menu offers what can be added where it was opened: on a space, equipment and a
// door / window on the side right-clicked (a hatch on its floor or roof); on any item of a structure,
// another space in that structure. And Rename for everything but a loft band.
//
// Mounted for real with react-dom/client over a minimal DOM (fixtures/miniDom).

import assert from "node:assert/strict";
import { test } from "node:test";

const dom = await import("../fixtures/miniDom");
await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const CellBuilderContextMenu = (await import("@/components/viewer/CellBuilderContextMenu")).default;

const box = { origin: [0, 0, 0] as [number, number, number], size: [2, 2, 2] as [number, number, number] };
useCellBuilderStore.setState({
  active: { modelId: "m", name: "demo/demo", revision: 1 },
  cells: {
    s1: { id: "s1", name: "S1", kind: "cell", group: "Hull", ...box },
    s2: { id: "s2", name: "S2", kind: "cell", ...box },
    e1: { id: "e1", name: "E1", kind: "equipment", params: { SPACE_NAME: "S1" }, ...box },
  },
  groups: [{ name: "Hull", blueprint: "bp" }],
  loftMembers: [],
} as never);

/** The menu's item labels, opened on `cellId` at side `faceIndex`. */
async function labels(cellId: string, faceIndex?: number): Promise<string[]> {
  useCellBuilderStore.getState().openContextMenu(10, 10, cellId, faceIndex);
  const root = createRoot(dom.container() as unknown as Element);
  try {
    await act(async () => root.render(React.createElement(CellBuilderContextMenu)));
    return dom.miniDocument.querySelectorAll('[role="menuitem"]').map((b) => b.textContent);
  } finally {
    await act(async () => root.unmount());
    useCellBuilderStore.getState().closeContextMenu();
  }
}

test("a space in a structure, right-clicked on a wall: equipment, door, window, and a space beside it", async () => {
  const items = await labels("s1", 1);
  for (const want of ["Rename…", "Add equipment…", "Add door (-X side)", "Add window (-X side)", "Add space to Hull"])
    assert.ok(items.includes(want), `${want} in ${items.join(" | ")}`);
});

test("on its roof, a hatch; in no structure, no Add space", async () => {
  const items = await labels("s2", 4);
  assert.ok(items.includes("Add hatch (roof)"), items.join(" | "));
  assert.ok(!items.some((l) => l.startsWith("Add space")));
  assert.ok(!items.some((l) => l.startsWith("Add door")));
});

test("equipment: Rename and a space in its structure, nothing seated in it", async () => {
  const items = await labels("e1");
  assert.ok(items.includes("Rename…") && items.includes("Add space to Hull"), items.join(" | "));
  assert.ok(!items.includes("Add equipment…"));
});
