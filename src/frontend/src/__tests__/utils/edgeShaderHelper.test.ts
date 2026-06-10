/** buildEdgeGeometryWithRangeIds: single-pass typed-array edge extraction with
 * EdgesGeometry semantics — position-welded soup vertices, boundary edges always
 * emitted, shared edges only past the dihedral threshold, per-vertex rangeId. */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { buildEdgeGeometryWithRangeIds } from "../../utils/mesh_select/EdgeShaderHelper";

/** Triangle-soup geometry (unique sequential indices, like the streamed GLB). */
function soup(triangles: number[][][]): THREE.BufferGeometry {
  const pos: number[] = [];
  for (const tri of triangles) for (const v of tri) pos.push(...v);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
  return g;
}

test("coplanar quad emits only its boundary; folded quad also emits the crease", () => {
  // Range A: two coplanar triangles sharing the diagonal (0,0,0)-(1,1,0).
  const flatQuad = [
    [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
    [[0, 0, 0], [1, 1, 0], [0, 1, 0]],
  ];
  // Range B (offset in y so welding can't bridge ranges): two triangles meeting
  // at 90 degrees along the shared edge (0,5,0)-(1,5,0).
  const folded = [
    [[0, 5, 0], [1, 5, 0], [1, 6, 0]],
    [[0, 5, 0], [1, 5, 1], [1, 5, 0]],
  ];
  const geom = soup([...flatQuad, ...folded]);
  const ranges = new Map<string, [number, number]>([
    ["A", [0, 6]],
    ["B", [6, 6]],
  ]);

  const { geometry, rangeIdToIndex } = buildEdgeGeometryWithRangeIds(geom, ranges);
  assert.deepEqual([...rangeIdToIndex.keys()], ["A", "B"]);

  const rangeAttr = geometry.getAttribute("rangeId") as THREE.BufferAttribute;
  const posAttr = geometry.getAttribute("position") as THREE.BufferAttribute;
  assert.equal(posAttr.count % 2, 0, "line segments come in vertex pairs");

  // Count segments per range.
  const segs = new Map<number, number>();
  for (let i = 0; i < rangeAttr.count; i += 2) {
    const r = rangeAttr.getX(i);
    segs.set(r, (segs.get(r) ?? 0) + 1);
  }
  // Flat quad: 4 boundary edges, diagonal suppressed (coplanar).
  assert.equal(segs.get(rangeIdToIndex.get("A")!), 4);
  // Folded pair: 4 boundary edges + the 90-degree crease.
  assert.equal(segs.get(rangeIdToIndex.get("B")!), 5);

  // The suppressed diagonal must not appear among range A's segments.
  const aIdx = rangeIdToIndex.get("A")!;
  for (let i = 0; i < rangeAttr.count; i += 2) {
    if (rangeAttr.getX(i) !== aIdx) continue;
    const p = [posAttr.getX(i), posAttr.getY(i), posAttr.getZ(i)];
    const q = [posAttr.getX(i + 1), posAttr.getY(i + 1), posAttr.getZ(i + 1)];
    const isDiag =
      (p[0] === 0 && p[1] === 0 && q[0] === 1 && q[1] === 1) ||
      (p[0] === 1 && p[1] === 1 && q[0] === 0 && q[1] === 0);
    assert.ok(!isDiag, "coplanar shared diagonal must be suppressed");
  }
});
