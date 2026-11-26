// A door/window cut from the 3D menu used to land in "unassigned" -- insertOpeningOnFace
// wrote no SPACE_NAME/SPACE_SIDE, so the Model panel filed it by geometry alone (containingCell),
// which an opening straddling a wall often loses to float noise. The opening stays
// USE_GLOBAL_COORDS (its compiled geometry is X/Y/Z/DX/DY/DZ, unaffected by SPACE_NAME); these are
// metadata for the tree, kept in sync on a rename and a move. See placementSlice.ts,
// cellsSlice.ts (renameCell, updateCell, translateCellAlongAxis) and utils/cellbuilder/snap.ts
// (nearestFaceStraddled).

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { nearestFaceStraddled } = await import("@/utils/cellbuilder/snap");

function model() {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells: {
      room: { id: "room", name: "Room", kind: "cell", group: "Hull", origin: [0, 0, 0], size: [4, 4, 3] },
    },
    groups: [{ name: "Hull", blueprint: "bp" }],
    loftMembers: [],
    activeGroup: null,
    selection: null,
    selectedCellIds: [],
  } as never);
}
const st = () => useCellBuilderStore.getState();
const latestOpening = () => {
  const ids = Object.keys(st().cells).filter((id) => id !== "room" && id !== "roomB" && st().cells[id].kind === "opening");
  return st().cells[ids[ids.length - 1]];
};

test("insertOpeningOnFace sets SPACE_NAME, SPACE_SIDE and STRUCTURE_NAME", () => {
  model();
  // faceIndex 1 is BOX_FACE_SIDES' "-X": a door cut into Room's -X wall.
  st().insertOpeningOnFace("room", 1, "door");
  const door = latestOpening();
  assert.equal(door.params?.SPACE_NAME, "Room");
  assert.equal(door.params?.SPACE_SIDE, "-X");
  assert.equal(door.params?.STRUCTURE_NAME, "Hull");
  assert.notEqual(door.openingFrame, "local", "stays a GLOBAL opening: the compiled geometry is unaffected");
});

test("insertOpeningOnFace on a +X face strips the sign from SPACE_SIDE", () => {
  model();
  st().insertOpeningOnFace("room", 0, "window"); // BOX_FACE_SIDES[0] = "+X"
  assert.equal(latestOpening().params?.SPACE_SIDE, "X");
});

test("a rename re-homes a GLOBAL opening's SPACE_NAME", () => {
  model();
  st().insertOpeningOnFace("room", 1, "door");
  const id = latestOpening().id;
  st().renameCell("room", "Lounge");
  assert.equal(st().cells[id].params?.SPACE_NAME, "Lounge");
});

test("nearestFaceStraddled: the nearest face within tolerance, among those whose rectangle contains the centre", () => {
  const room = { name: "Room", origin: [0, 0, 0] as [number, number, number], size: [4, 4, 3] as [number, number, number] };
  // An opening straddling Room's -X wall (centre.x == 0, within the door's own half-thickness).
  const onWall = { origin: [-0.15, 1, 0] as [number, number, number], size: [0.3, 2, 2] as [number, number, number] };
  const hit = nearestFaceStraddled(onWall, [room]);
  assert.ok(hit);
  assert.equal(hit!.cell.name, "Room");
  assert.equal(hit!.side.label, "-X");

  // Dragged well clear of every candidate: no hit.
  const away = { origin: [20, 20, 0] as [number, number, number], size: [0.3, 2, 2] as [number, number, number] };
  assert.equal(nearestFaceStraddled(away, [room]), null);
});

test("moving a global opening off its space clears SPACE_NAME/SPACE_SIDE; onto another's face re-homes it", () => {
  model();
  useCellBuilderStore.setState({
    // A gap from Room's own +X face (x=4) so a move onto RoomB's -X face (x=5) is unambiguous.
    cells: {
      ...st().cells,
      roomB: { id: "roomB", name: "RoomB", kind: "cell", group: "Hull", origin: [5, 0, 0], size: [4, 4, 3] },
    },
  } as never);
  st().insertOpeningOnFace("room", 1, "door"); // Room's -X wall
  const id = latestOpening().id;
  assert.equal(st().cells[id].params?.SPACE_NAME, "Room");

  // Move it far away: no space's face is anywhere near it any more.
  st().updateCell(id, { origin: [40, 40, 0] });
  assert.equal(st().cells[id].params?.SPACE_NAME, undefined);
  assert.equal(st().cells[id].params?.SPACE_SIDE, undefined);

  // Move it onto RoomB's -X face (x = 5): well clear of Room's own +X face (x = 4).
  const opening = st().cells[id];
  st().updateCell(id, { origin: [5 - opening.size[0] / 2, 1, 0] });
  assert.equal(st().cells[id].params?.SPACE_NAME, "RoomB");
  assert.equal(st().cells[id].params?.SPACE_SIDE, "-X");
});

test("translateCellAlongAxis also keeps a global opening's SPACE_NAME in sync", () => {
  model();
  st().insertOpeningOnFace("room", 1, "door");
  const id = latestOpening().id;
  // Nudge it along the wall (Y axis): still on Room's -X face.
  st().translateCellAlongAxis(id, 1, 0.5);
  assert.equal(st().cells[id].params?.SPACE_NAME, "Room");
  // Nudge it far along X, off the wall entirely.
  st().translateCellAlongAxis(id, 0, 40);
  assert.equal(st().cells[id].params?.SPACE_NAME, undefined);
});
