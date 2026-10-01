/**
 * The edge overlay's core, as the worker runs it: a cube as a triangle soup (every triangle owns
 * its vertices, as a tessellated GLB does) has exactly its 12 edges, the face diagonals dropping
 * out as coplanar; each output vertex names the draw range it came from.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { computeEdgeArrays } from "../../utils/mesh_select/edgeCore";

/** A unit cube's 12 triangles, unshared. */
function cubeSoup(offset = 0): { pos: number[]; tris: number } {
  const c = [
    [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
  ];
  const quads = [
    [0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [0, 4, 7, 3],
  ];
  const pos: number[] = [];
  for (const [a, b, cc, d] of quads) {
    for (const v of [a, b, cc, a, cc, d]) pos.push(c[v][0] + offset, c[v][1], c[v][2]);
  }
  return { pos, tris: 12 };
}

const cos1deg = Math.cos((1 * Math.PI) / 180);

test("a cube soup has its 12 edges, and no face diagonals", () => {
  const { pos } = cubeSoup();
  const index = Uint32Array.from({ length: pos.length / 3 }, (_, i) => i);
  const out = computeEdgeArrays(Float32Array.from(pos), index, [[0, index.length]], cos1deg);
  assert.equal(out.positions.length / 6, 12, "segments");
  assert.equal(out.rangeIdx.length, 24, "one range index per output vertex");
  for (let s = 0; s < out.positions.length; s += 6) {
    const d = [0, 1, 2].filter((k) => out.positions[s + k] !== out.positions[s + 3 + k]).length;
    assert.equal(d, 1, "an edge of a cube differs in exactly one coordinate");
  }
});

test("each range is welded on its own, and its vertices carry its index", () => {
  const a = cubeSoup(0).pos;
  const b = cubeSoup(5).pos;
  const pos = Float32Array.from([...a, ...b]);
  const index = Uint32Array.from({ length: pos.length / 3 }, (_, i) => i);
  const half = index.length / 2;
  const out = computeEdgeArrays(pos, index, [[0, half], [half, half]], cos1deg);
  assert.equal(out.positions.length / 6, 24);
  assert.deepEqual([...new Set(out.rangeIdx)].sort(), [0, 1]);
  assert.equal(out.rangeIdx.filter((r) => r === 1).length, 24);
});

test("an empty range emits nothing", () => {
  const out = computeEdgeArrays(new Float32Array(0), new Uint32Array(0), [[0, 0]], cos1deg);
  assert.equal(out.positions.length, 0);
  assert.equal(out.rangeIdx.length, 0);
});
