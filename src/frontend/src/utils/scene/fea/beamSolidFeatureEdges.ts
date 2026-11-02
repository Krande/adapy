// The beam-solid element edges as index pairs, and the length their fade is measured by.
//
// Pure arrays in, arrays out, no three.js: it runs in beamSolidEdges.worker.ts, since a
// large model's solids are millions of vertices and building their edges took seconds on
// the main thread. beamSolidEdges.ts turns the result into the overlay.

/** A fold sharper than this is an edge. 30°: a 12-facet round section folds by
 *  exactly 30° and stays smooth; a rectangular corner (90°) does not. */
export const FEATURE_ANGLE_DEG = 31;

/**
 * Feature edges of each element of a triangle mesh, as index pairs into
 * `positions`.
 *
 * Vertices are matched by position within an element (a solid splits them per
 * face for flat shading). An edge is kept when only one triangle of the element
 * has it (an open boundary) or its two triangles' normals differ by more than the
 * feature angle. Degenerate triangles are ignored. `owners`, when given, gets
 * the ordinal of the range each pair came from.
 */
export function beamSolidFeatureEdges(
    positions: ArrayLike<number>,
    index: ArrayLike<number>,
    ranges: Iterable<[number, number]>,
    featureAngleDeg = FEATURE_ANGLE_DEG,
    owners?: number[],
): Uint32Array {
    const cosLimit = Math.cos((featureAngleDeg * Math.PI) / 180);
    const out: number[] = [];
    // Matched to a micron (model units are metres): a section's seam vertex is
    // computed twice, from angle 0 and 2π, and the two differ in the last bits.
    const q = (x: number) => Math.round(x * 1e6);
    // Reused per element, all numeric: string keys and an object per edge made
    // this take seconds on a few million vertices.
    const canon = new Map<number, number>(); // position hash -> canonical id (open addressing on the hash)
    const ids: number[] = []; // canonical id -> a vertex
    const qs: number[] = []; // canonical id -> quantised xyz
    const edges = new Map<number, number>(); // (lo, hi) -> edge slot
    let eA: number[] = [], eB: number[] = [], eN: number[] = [], eNx: number[] = [], eNy: number[] = [], eNz: number[] = [];
    let eSharp: boolean[] = [];

    const idOf = (v: number) => {
        const x = q(positions[v * 3]), y = q(positions[v * 3 + 1]), z = q(positions[v * 3 + 2]);
        let h = (Math.imul(x, 73856093) ^ Math.imul(y, 19349663) ^ Math.imul(z, 83492791)) | 0;
        for (;;) {
            const id = canon.get(h);
            if (id === undefined) {
                const nid = ids.length;
                canon.set(h, nid);
                ids.push(v);
                qs.push(x, y, z);
                return nid;
            }
            if (qs[id * 3] === x && qs[id * 3 + 1] === y && qs[id * 3 + 2] === z) return id;
            h = (h + 1) | 0;
        }
    };
    const addEdge = (p: number, r: number, nx: number, ny: number, nz: number) => {
        const lo = p < r ? p : r, hi = p < r ? r : p;
        const k = lo * 0x200000 + hi;
        const slot = edges.get(k);
        if (slot === undefined) {
            edges.set(k, eA.length);
            eA.push(lo); eB.push(hi); eN.push(1); eNx.push(nx); eNy.push(ny); eNz.push(nz); eSharp.push(false);
        } else {
            eN[slot]++;
            if (eNx[slot] * nx + eNy[slot] * ny + eNz[slot] * nz < cosLimit) eSharp[slot] = true;
        }
    };

    let ordinal = -1;
    for (const [start, count] of ranges) {
        ordinal++;
        canon.clear();
        edges.clear();
        ids.length = 0;
        qs.length = 0;
        eA = []; eB = []; eN = []; eNx = []; eNy = []; eNz = []; eSharp = [];
        const end = Math.min(start + count, index.length);
        for (let i = start; i + 2 < end; i += 3) {
            const va = index[i], vb = index[i + 1], vc = index[i + 2];
            const ia = idOf(va), ib = idOf(vb), ic = idOf(vc);
            if (ia === ib || ib === ic || ia === ic) continue;
            const ux = positions[vb * 3] - positions[va * 3];
            const uy = positions[vb * 3 + 1] - positions[va * 3 + 1];
            const uz = positions[vb * 3 + 2] - positions[va * 3 + 2];
            const wx = positions[vc * 3] - positions[va * 3];
            const wy = positions[vc * 3 + 1] - positions[va * 3 + 1];
            const wz = positions[vc * 3 + 2] - positions[va * 3 + 2];
            let nx = uy * wz - uz * wy;
            let ny = uz * wx - ux * wz;
            let nz = ux * wy - uy * wx;
            const l = Math.hypot(nx, ny, nz);
            if (!(l > 0)) continue;
            nx /= l; ny /= l; nz /= l;
            addEdge(ia, ib, nx, ny, nz);
            addEdge(ib, ic, nx, ny, nz);
            addEdge(ic, ia, nx, ny, nz);
        }
        for (let e = 0; e < eA.length; e++) {
            if (eN[e] === 1 || eSharp[e]) {
                out.push(ids[eA[e]], ids[eB[e]]);
                owners?.push(ordinal);
            }
        }
    }
    return Uint32Array.from(out);
}

/**
 * The median element length of a sample of `ranges`: the longest side of each
 * element's box. What the edges' fade is measured by.
 *
 * Not the median edge length, as for the shell grid: a beam solid's edges are
 * mostly the facets of its end-cap outlines, centimetres long on a tubular, and
 * faded by those the overlay only showed zoomed far into one member. On screen,
 * a beam's element boundaries are as far apart as its elements are long.
 */
export function typicalElementLength(
    positions: ArrayLike<number>,
    index: ArrayLike<number>,
    ranges: ReadonlyArray<[number, number]>,
): number {
    const step = Math.max(1, Math.floor(ranges.length / 2000));
    const lengths: number[] = [];
    for (let r = 0; r < ranges.length; r += step) {
        const [start, count] = ranges[r];
        let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
        const end = Math.min(start + count, index.length);
        for (let i = start; i < end; i++) {
            const v = index[i] * 3;
            const x = positions[v], y = positions[v + 1], z = positions[v + 2];
            if (x < x0) x0 = x; if (x > x1) x1 = x;
            if (y < y0) y0 = y; if (y > y1) y1 = y;
            if (z < z0) z0 = z; if (z > z1) z1 = z;
        }
        if (end > start) lengths.push(Math.max(x1 - x0, y1 - y0, z1 - z0));
    }
    lengths.sort((a, b) => a - b);
    return lengths[lengths.length >> 1] ?? 0;
}

export interface BeamSolidEdgeData {
    /** Index pairs into the solid's source-numbered positions. */
    pairs: Uint32Array;
    /** Per pair, the ordinal of the range it outlines. */
    owner: Uint32Array;
    /** `typicalElementLength` of the ranges. */
    elementLength: number;
}

/** Everything the overlay needs, from the solid's source arrays and its ranges. */
export function computeBeamSolidEdges(
    positions: ArrayLike<number>,
    index: ArrayLike<number>,
    ranges: ReadonlyArray<[number, number]>,
): BeamSolidEdgeData {
    const owners: number[] = [];
    const pairs = beamSolidFeatureEdges(positions, index, ranges, FEATURE_ANGLE_DEG, owners);
    return {pairs, owner: Uint32Array.from(owners), elementLength: typicalElementLength(positions, index, ranges)};
}
