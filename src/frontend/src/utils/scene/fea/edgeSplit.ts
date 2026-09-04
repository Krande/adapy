// Separating beam edges from shell edges in the element-edge wireframe.
//
// The bake writes every element's edges as one deduped list, plus a second list
// naming the ones that belong to line elements. Drawing both as-is would paint
// those pairs twice at the same depth, which is a z-fight whose winner the driver
// picks — so the beam edges are removed from the main set before it is drawn.
//
// A key per pair rather than a nested scan: an edge list is tens of thousands of
// pairs and the beam list can be thousands of them, and O(n·m) on a load is the
// kind of thing that only shows up on the largest model anyone has.

/** Pack an unordered vertex pair into one comparable number.
 *
 * The bake sorts each pair before deduping, so the two lists already agree on
 * orientation — but sorting here too costs nothing and means this does not depend
 * on that staying true.
 *
 * `Number` rather than a string key: with 32-bit vertex indices the packed value
 * exceeds 2^53 only past ~94 million vertices, far beyond anything that reaches
 * this renderer, and a numeric Set is markedly faster than a string one.
 */
function edgeKey(a: number, b: number): number {
    const lo = a < b ? a : b;
    const hi = a < b ? b : a;
    return lo * 0x100000000 + hi;
}

/**
 * `all` minus the pairs in `remove`.
 *
 * Both are flat index arrays, two entries per edge. Returns `all` unchanged when
 * `remove` is empty, so the common case allocates nothing.
 */
export function withoutEdges(all: Uint32Array, remove: Uint32Array): Uint32Array {
    if (remove.length === 0 || all.length === 0) return all;

    const drop = new Set<number>();
    for (let i = 0; i + 1 < remove.length; i += 2) {
        drop.add(edgeKey(remove[i], remove[i + 1]));
    }

    const kept = new Uint32Array(all.length);
    let n = 0;
    for (let i = 0; i + 1 < all.length; i += 2) {
        if (drop.has(edgeKey(all[i], all[i + 1]))) continue;
        kept[n++] = all[i];
        kept[n++] = all[i + 1];
    }
    return n === all.length ? all : kept.subarray(0, n);
}
