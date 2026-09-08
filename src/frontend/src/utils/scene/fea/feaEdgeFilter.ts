/** Cut an element-boundary wireframe down to the elements that are still visible.
 *
 *  The FEA loader draws element outlines as ONE LineSegments over the whole model: a
 *  deduped list of vertex-index pairs with no record of which element each edge came from
 *  (a shared edge belongs to two). Isolating a group therefore leaves the hidden elements
 *  still outlined — fine as a deliberate ghost, wrong when you asked for just the group.
 *
 *  The association is recoverable, though, because the mesh's draw ranges say which slice
 *  of the TRIANGLE index buffer each element owns. Every element-boundary edge is an edge
 *  of one of that element's triangles, so collecting the triangle edges of the visible
 *  elements gives a superset to test the overlay's pairs against. An overlay edge is kept
 *  when it belongs to at least one visible element — which is what you want at the cut
 *  face too, where the boundary between shown and hidden should stay drawn.
 *
 *  Pure and renderer-free: it takes typed arrays in and returns one out.
 */

/** Order-independent key for an edge between two vertex indices.
 *
 *  Pairs are packed into one number rather than a "a,b" string: this runs over every
 *  triangle of every visible element on each selection change, and string keys made that
 *  allocation-bound on a real deck. Safe while vertex counts stay under ~94 million, which
 *  is far past the point where the browser has other problems.
 */
const edgeKey = (a: number, b: number): number => (a < b ? a * 0x4000000 + b : b * 0x4000000 + a);

/**
 * @param triIndex   The mesh's triangle index buffer (three vertex indices per face).
 * @param drawRanges Element id → [start, count] into ``triIndex``.
 * @param keep       Element ids that stay visible. Ids naming no range are ignored, which
 *                   is how node members (``P{n}``) pass through harmlessly.
 * @param fullEdges  The overlay's untouched index buffer: consecutive vertex-index pairs.
 * @returns A filtered copy, or null when there is nothing to work with — callers leave the
 *          overlay alone rather than guessing.
 */
export function visibleEdgeIndex(
    triIndex: ArrayLike<number> | null,
    drawRanges: ReadonlyMap<string, [number, number]>,
    keep: ReadonlySet<string>,
    fullEdges: ArrayLike<number>,
): Uint32Array | null {
    if (!triIndex || fullEdges.length === 0) return null;

    const allowed = new Set<number>();
    for (const id of keep) {
        const range = drawRanges.get(id);
        if (!range) continue;
        const [start, count] = range;
        const end = Math.min(start + count, triIndex.length);
        // Step by whole triangles. A range whose count is not a multiple of 3 would
        // otherwise read across into the next element's vertices and mark edges that
        // belong to something hidden.
        for (let i = start; i + 2 < end; i += 3) {
            const a = triIndex[i];
            const b = triIndex[i + 1];
            const c = triIndex[i + 2];
            allowed.add(edgeKey(a, b));
            allowed.add(edgeKey(b, c));
            allowed.add(edgeKey(c, a));
        }
    }
    // Nothing recognised — every id was a node, or from another model. Filtering to
    // nothing would blank the wireframe, so decline instead.
    if (allowed.size === 0) return null;

    const out = new Uint32Array(fullEdges.length);
    let n = 0;
    for (let i = 0; i + 1 < fullEdges.length; i += 2) {
        const u = fullEdges[i];
        const v = fullEdges[i + 1];
        if (allowed.has(edgeKey(u, v))) {
            out[n++] = u;
            out[n++] = v;
        }
    }
    return out.subarray(0, n);
}
