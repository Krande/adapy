/** Which element-boundary edges to draw when some elements are hidden.
 *
 *  The FEA loader draws element outlines as ONE LineSegments over the whole model: a
 *  deduped list of vertex-index pairs with no record of which element each edge came from
 *  (a shared edge belongs to two). The association is recoverable from the mesh's draw
 *  ranges, which say which slice of the TRIANGLE index buffer each element owns: an edge is
 *  owned by the elements whose triangles use both its ends. An edge is drawn while any of
 *  its owners is visible -- so the boundary between shown and hidden stays drawn -- and an
 *  edge no element owns (a beam drawn only as a line) is never touched.
 *
 *  Pure and renderer-free: typed arrays in, typed arrays out.
 */

/** Which draw ranges own each edge, CSR: edge i's owners are `owners[offsets[i] .. offsets[i + 1]]`,
 *  indices into `ids`. */
export interface EdgeOwners {
    ids: string[];
    offsets: Uint32Array;
    owners: Uint32Array;
}

/** The draw ranges (`ranges`, over `index`) whose triangles contain both ends of each edge. */
export function edgeOwners(
    ranges: ReadonlyMap<string, readonly [number, number]>,
    index: ArrayLike<number>,
    vertexCount: number,
    edges: ArrayLike<number>,
): EdgeOwners {
    const ids = [...ranges.keys()];
    // Vertex -> the ranges that use it (CSR, each range once per vertex).
    const count = new Uint32Array(vertexCount + 1);
    const stamp = new Int32Array(vertexCount).fill(-1);
    ids.forEach((id, r) => {
        const [start, n] = ranges.get(id)!;
        for (let i = start; i < start + n; i++) {
            const v = index[i];
            if (stamp[v] !== r) {
                stamp[v] = r;
                count[v + 1]++;
            }
        }
    });
    for (let v = 0; v < vertexCount; v++) count[v + 1] += count[v];
    const vertexRanges = new Uint32Array(count[vertexCount]);
    const fill = count.slice(0, vertexCount);
    stamp.fill(-1);
    ids.forEach((id, r) => {
        const [start, n] = ranges.get(id)!;
        for (let i = start; i < start + n; i++) {
            const v = index[i];
            if (stamp[v] !== r) {
                stamp[v] = r;
                vertexRanges[fill[v]++] = r;
            }
        }
    });
    // Edge -> ranges using both ends.
    const pairs = edges.length / 2;
    const offsets = new Uint32Array(pairs + 1);
    const owners: number[] = [];
    for (let e = 0; e < pairs; e++) {
        const a = edges[e * 2], b = edges[e * 2 + 1];
        for (let i = count[a]; i < count[a + 1]; i++) {
            const r = vertexRanges[i];
            for (let j = count[b]; j < count[b + 1]; j++) {
                if (vertexRanges[j] === r) {
                    owners.push(r);
                    break;
                }
            }
        }
        offsets[e + 1] = owners.length;
    }
    return {ids, offsets, owners: Uint32Array.from(owners)};
}

/** Write `all` into `out`, with every edge whose owners are all hidden collapsed to a zero-length pair. */
export function maskHiddenEdges(
    all: ArrayLike<number>,
    owned: EdgeOwners,
    isHidden: (id: string) => boolean,
    out: {[i: number]: number},
): void {
    const hidden = owned.ids.map(isHidden);
    for (let e = 0; e < all.length / 2; e++) {
        const a = all[e * 2], b = all[e * 2 + 1];
        const start = owned.offsets[e], end = owned.offsets[e + 1];
        let shown = start === end; // no owner: leave it be
        for (let i = start; i < end && !shown; i++) shown = !hidden[owned.owners[i]];
        out[e * 2] = a;
        out[e * 2 + 1] = shown ? b : a;
    }
}
