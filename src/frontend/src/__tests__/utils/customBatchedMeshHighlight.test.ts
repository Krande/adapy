/** updateSelectionGroups edge-highlight dispatch: selection is broadcast to every
 * CustomBatchedMesh in the model, but each mesh's rangeIdToIndex only covers the
 * objects in its own buffer. A mesh must highlight only a selected id it actually
 * owns and otherwise clear to -1 — never push undefined (coerced to 0) into the
 * uHighlighted uniform, which used to light up each buffer's range-index-0 object. */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { CustomBatchedMesh } from "../../utils/mesh_select/CustomBatchedMesh";

/** Triangle-soup geometry (unique sequential indices, like the streamed GLB). */
function soup(triangles: number[][][]): THREE.BufferGeometry {
  const pos: number[] = [];
  for (const tri of triangles) for (const v of tri) pos.push(...v);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
  return g;
}

/** makeEdgeShaderMaterial only reads renderer.capabilities.maxTextureSize. */
const mockRenderer = { capabilities: { maxTextureSize: 4096 } } as unknown as THREE.WebGLRenderer;

/** Mesh owning two ranges A,B, with its edge overlay (and edgeMaterial) built. */
function meshWithOverlay(): CustomBatchedMesh {
  const geom = soup([
    [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
    [[0, 0, 0], [1, 1, 0], [0, 1, 0]],
    [[0, 5, 0], [1, 5, 0], [1, 6, 0]],
    [[0, 5, 0], [1, 5, 1], [1, 5, 0]],
  ]);
  const ranges = new Map<string, [number, number]>([
    ["A", [0, 6]],
    ["B", [6, 6]],
  ]);
  const mesh = new CustomBatchedMesh(geom, new THREE.MeshBasicMaterial(), ranges, "buf", true, null);
  mesh.getEdgeOverlay(mockRenderer); // builds edgeMaterial + rangeIdToIndex
  return mesh;
}

const uHighlighted = (m: CustomBatchedMesh): number =>
  (m as any).edgeMaterial.uniforms.uHighlighted.value;
const indexOf = (m: CustomBatchedMesh, id: string): number =>
  (m as any).rangeIdToIndex.get(id);

test("selecting an owned range highlights that range's edges", () => {
  const m = meshWithOverlay();
  m.updateSelectionGroups(["B"]);
  assert.equal(uHighlighted(m), indexOf(m, "B"));
});

test("selecting a range this mesh does NOT own clears highlight to -1", () => {
  const m = meshWithOverlay();
  // Regression: previously rangeIdToIndex.get("Z")! pushed undefined -> 0,
  // lighting up this buffer's range-index-0 object ("A") on every selection.
  m.updateSelectionGroups(["Z"]);
  assert.equal(uHighlighted(m), -1);
});

test("a stale highlight is cleared when the next selection isn't owned here", () => {
  const m = meshWithOverlay();
  m.updateSelectionGroups(["A"]);
  assert.equal(uHighlighted(m), indexOf(m, "A"));
  m.updateSelectionGroups(["Z"]); // object in some other buffer
  assert.equal(uHighlighted(m), -1);
});

test("multi-object selection highlights the first id this mesh owns", () => {
  const m = meshWithOverlay();
  // "Z" lives in another buffer, "A" lives here — this mesh should light A.
  m.updateSelectionGroups(["Z", "A"]);
  assert.equal(uHighlighted(m), indexOf(m, "A"));
});

test("empty selection clears highlight", () => {
  const m = meshWithOverlay();
  m.updateSelectionGroups(["A"]);
  m.updateSelectionGroups([]);
  assert.equal(uHighlighted(m), -1);
});
