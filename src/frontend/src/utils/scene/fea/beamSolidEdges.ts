// The beam-solid mesh's element-edge wireframe.
//
// The beam-solid artefacts carry triangles and a per-beam draw range, no edges: a
// lattice boom drawn as solids was a smooth shaded mass where one member ran into
// the next, with nothing to read the elements off. This builds the edges the shell
// grid has, from the triangles themselves: per element (draw range), every edge
// where the surface folds by more than `FEATURE_ANGLE_DEG` -- the corners of a
// box or I profile, and the outline of each end cap, which is the element
// boundary along the member. A round section's facets fold by less than that and
// stay smooth; its end caps still outline it.
//
// Per element, so two members meeting at a joint keep their own outlines (their
// end caps coincide and would otherwise cancel as an interior edge).
//
// The overlay holds its own position buffer in the solid's SOURCE vertex
// numbering. The solid's own buffer is swapped for an element-local expansion when
// an element field is painted, and three.js ignores a morph whose length does not
// match; so, like the main mesh's wireframe, the overlay gets its own morph
// (setSourceMorph) from the source-numbered beam-solid displacement.

import * as THREE from "three";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {FEA_EDGE_COLOR} from "./edgeColors";
import {elementLocalMap} from "./elementLocalGeometry";
import {setSourceMorph} from "./sourceMorph";

export const BEAM_SOLID_EDGES_NAME = "fea-beam-solid-edges";

/** A fold sharper than this is an edge. 30°: a 12-facet round section folds by
 *  exactly 30° and stays smooth; a rectangular corner (90°) does not. */
const FEATURE_ANGLE_DEG = 31;

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

/** Which element (draw-range id) each edge pair outlines, and the full pair list
 *  to restore from. */
export interface BeamSolidEdgeOwners {
    all: Uint32Array;
    rangeIds: string[];
    owner: Uint32Array;
}

/** Collapse the pairs of hidden elements to a point (both ends the first
 *  vertex), in `out`; the buffer keeps its size. */
export function maskBeamSolidEdges(
    owners: BeamSolidEdgeOwners,
    isHidden: (rangeId: string) => boolean,
    out: Uint32Array,
): void {
    const hidden = owners.rangeIds.map(isHidden);
    const {all, owner} = owners;
    for (let p = 0; p < owner.length; p++) {
        const a = all[p * 2];
        out[p * 2] = a;
        out[p * 2 + 1] = hidden[owner[p]] ? a : all[p * 2 + 1];
    }
}

/** The overlay on `solids`, or null. */
export function beamSolidEdges(solids: THREE.Object3D): THREE.LineSegments | null {
    const child = solids.getObjectByName(BEAM_SOLID_EDGES_NAME);
    return child instanceof THREE.LineSegments ? child : null;
}

/**
 * Build the overlay on `solids` once (a no-op when it exists). `basePositions`
 * are the solid's undeformed positions in source numbering. The index must be in
 * that numbering too: the loader keeps the one the solid arrived with as
 * `userData.__sourceIndex`, since painting an element field swaps in the
 * expansion's; failing that, the geometry's own while it is unexpanded.
 */
export function ensureBeamSolidEdges(
    solids: THREE.Mesh & {drawRanges?: Map<string, [number, number]>},
    basePositions: Float32Array,
): THREE.LineSegments | null {
    const existing = beamSolidEdges(solids);
    if (existing) return existing;
    const geometry = solids.geometry;
    const index = (solids.userData.__sourceIndex as ArrayLike<number> | null | undefined)
        ?? (elementLocalMap(geometry) ? null : geometry.getIndex()?.array);
    if (!index || !solids.drawRanges) return null;
    const nSource = basePositions.length / 3;

    const t0 = performance.now();
    const rangeIds = [...solids.drawRanges.keys()];
    const owners: number[] = [];
    const pairs = beamSolidFeatureEdges(basePositions, index, solids.drawRanges.values(), FEATURE_ANGLE_DEG, owners);
    if (!pairs.length) return null;

    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", new THREE.BufferAttribute(Float32Array.from(basePositions), 3));
    lineGeom.setIndex(new THREE.BufferAttribute(pairs, 1));
    // A zero morph from the start, so linkLineMorphToMesh -- which hands the
    // parent's morph to any wireframe child without one -- leaves this alone.
    lineGeom.morphAttributes.position = [new THREE.BufferAttribute(new Float32Array(nSource * 3), 3)];
    lineGeom.morphTargetsRelative = true;
    const segments = new THREE.LineSegments(
        lineGeom,
        new THREE.LineBasicMaterial({color: FEA_EDGE_COLOR, depthTest: true, transparent: true}),
    );
    segments.name = BEAM_SOLID_EDGES_NAME;
    // As the shell grid: above face overlays, below the selection highlight, and
    // on layer 1 (drawn, never picked -- a click goes to the solid behind it).
    segments.renderOrder = 3;
    segments.layers.set(1);
    segments.frustumCulled = false;
    clipWithModel(segments);
    segments.visible = useFeaAnimationStore.getState().beamSolidEdgesVisible;
    const ownership: BeamSolidEdgeOwners = {all: Uint32Array.from(pairs), rangeIds, owner: Uint32Array.from(owners)};
    segments.userData.beamSolidEdgeOwners = ownership;
    // Elements hidden before the solids first showed.
    const hiddenOf = solids as unknown as {getHiddenRanges?: () => ReadonlySet<string>; isRangeHidden?: (id: string) => boolean};
    if (hiddenOf.getHiddenRanges?.().size && hiddenOf.isRangeHidden) {
        maskBeamSolidEdges(ownership, (id) => hiddenOf.isRangeHidden!(id), pairs);
    }
    solids.add(segments);

    // The warp already installed on the solid, if any.
    const current = solids.userData.__beamSourceDisplacement as Float32Array | undefined;
    setSourceMorph(solids, BEAM_SOLID_EDGES_NAME, current ?? new Float32Array(nSource * 3));
    // eslint-disable-next-line no-console
    console.debug(`[fea] beam-solid edges: ${pairs.length / 2} in ${(performance.now() - t0).toFixed(0)} ms`);
    return segments;
}

/** Move the overlay with the solid: `sourceDisplacement` is the solid's morph
 *  delta in source numbering. Kept on the solid for an overlay built later. */
export function warpBeamSolidEdges(solids: THREE.Mesh, sourceDisplacement: Float32Array): void {
    solids.userData.__beamSourceDisplacement = sourceDisplacement;
    setSourceMorph(solids, BEAM_SOLID_EDGES_NAME, sourceDisplacement);
}

/** The source-numbered displacement behind a render-numbered morph of `geometry`
 *  (the inverse of the element-local expansion; identity when unexpanded). */
export function sourceDisplacementFromRender(
    geometry: THREE.BufferGeometry,
    renderMorph: Float32Array,
    nSource: number,
): Float32Array {
    const map = elementLocalMap(geometry);
    if (!map) return renderMorph.length === nSource * 3 ? renderMorph : new Float32Array(nSource * 3);
    const out = new Float32Array(nSource * 3);
    for (let r = 0; r < map.length; r++) {
        const s = map[r] * 3;
        out[s] = renderMorph[r * 3];
        out[s + 1] = renderMorph[r * 3 + 1];
        out[s + 2] = renderMorph[r * 3 + 2];
    }
    return out;
}

/** Move the overlay to a render-numbered morph of the solid (what a recorded
 *  playback frame keeps). */
export function warpBeamSolidEdgesFromRender(solids: THREE.Mesh, renderMorph: Float32Array): void {
    const overlay = beamSolidEdges(solids);
    const n = overlay?.geometry.getAttribute("position")?.count;
    if (!overlay || !n) return;
    setSourceMorph(solids, BEAM_SOLID_EDGES_NAME, sourceDisplacementFromRender(solids.geometry, renderMorph, n));
}
