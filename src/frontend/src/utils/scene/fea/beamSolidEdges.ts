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
//
// Built in a worker (requestBeamSolidEdges): on a large model the solids are millions of
// vertices, and building their edges froze the page for seconds when they first showed.

import * as THREE from "three";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {requestRender} from "@/state/perfStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {computeBeamSolidEdges, type BeamSolidEdgeData} from "./beamSolidFeatureEdges";
import {FEA_EDGE_COLOR} from "./edgeColors";
import {elementLocalMap} from "./elementLocalGeometry";
import {setSourceMorph} from "./sourceMorph";
import {fadeAtSpacing} from "./streaming/elementEdges";

export {beamSolidFeatureEdges, typicalElementLength} from "./beamSolidFeatureEdges";

export const BEAM_SOLID_EDGES_NAME = "fea-beam-solid-edges";

type BeamSolids = THREE.Mesh & {drawRanges?: Map<string, [number, number]>};

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

/** What the edges are built from: the solid's index in source numbering, and its draw
 *  ranges with their ids. The index is the one the loader kept as
 *  `userData.__sourceIndex`, since painting an element field swaps in the expansion's;
 *  failing that, the geometry's own while it is unexpanded. */
function edgeInputs(solids: BeamSolids): {index: ArrayLike<number>; rangeIds: string[]; ranges: [number, number][]} | null {
    const geometry = solids.geometry;
    const index = (solids.userData.__sourceIndex as ArrayLike<number> | null | undefined)
        ?? (elementLocalMap(geometry) ? null : geometry.getIndex()?.array);
    if (!index || !solids.drawRanges) return null;
    return {index, rangeIds: [...solids.drawRanges.keys()], ranges: [...solids.drawRanges.values()]};
}

/**
 * Build the overlay on `solids` now, on this thread (a no-op when it exists).
 * `basePositions` are the solid's undeformed positions in source numbering. What
 * the viewer calls is `requestBeamSolidEdges`; this is its fallback where no worker
 * can be spawned.
 */
export function ensureBeamSolidEdges(solids: BeamSolids, basePositions: Float32Array): THREE.LineSegments | null {
    const existing = beamSolidEdges(solids);
    if (existing) return existing;
    const inputs = edgeInputs(solids);
    if (!inputs) return null;
    const t0 = performance.now();
    const data = computeBeamSolidEdges(basePositions, inputs.index, inputs.ranges);
    const segments = attachBeamSolidEdges(solids, basePositions, data, inputs.rangeIds);
    // eslint-disable-next-line no-console
    console.debug(`[fea] beam-solid edges: ${data.pairs.length / 2} in ${(performance.now() - t0).toFixed(0)} ms`);
    return segments;
}

/**
 * Have the overlay built on `solids`, off the main thread, and attached when it is
 * ready; a no-op when it exists or is being built. Visibility is read from the store
 * when it lands, so a toggle flipped meanwhile is honoured.
 */
export function requestBeamSolidEdges(solids: BeamSolids, basePositions: Float32Array): void {
    if (beamSolidEdges(solids) || solids.userData.__beamSolidEdgesPending) return;
    const inputs = edgeInputs(solids);
    if (!inputs) return;
    solids.userData.__beamSolidEdgesPending = true;
    const t0 = performance.now();
    void import("./beamSolidEdgesInWorker")
        .then(({computeBeamSolidEdgesInWorker}) =>
            computeBeamSolidEdgesInWorker(basePositions, inputs.index, inputs.ranges))
        .catch((err) => {
            // eslint-disable-next-line no-console
            console.warn("[fea] beam-solid edge worker unavailable; building on the main thread:", err);
            return computeBeamSolidEdges(basePositions, inputs.index, inputs.ranges);
        })
        .then((data) => {
            solids.userData.__beamSolidEdgesPending = false;
            // Gone meanwhile (another result loaded): nothing to hang them on.
            if (!solids.parent || beamSolidEdges(solids)) return;
            attachBeamSolidEdges(solids, basePositions, data, inputs.rangeIds);
            requestRender();
            // eslint-disable-next-line no-console
            console.debug(`[fea] beam-solid edges: ${data.pairs.length / 2} in ${(performance.now() - t0).toFixed(0)} ms (worker)`);
        });
}

function attachBeamSolidEdges(
    solids: BeamSolids,
    basePositions: Float32Array,
    data: BeamSolidEdgeData,
    rangeIds: string[],
): THREE.LineSegments | null {
    const {pairs} = data;
    if (!pairs.length) return null;
    const nSource = basePositions.length / 3;

    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", new THREE.BufferAttribute(basePositions.slice(), 3));
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
    // The shell grid's fade with on-screen density, by element length rather than
    // edge length (see typicalElementLength), at the overlay's own (source-numbered,
    // undeformed) positions -- the solid's buffer may be an element-local expansion.
    fadeAtSpacing(segments, segments, data.elementLength);
    segments.visible = useFeaAnimationStore.getState().beamSolidEdgesVisible;
    const ownership: BeamSolidEdgeOwners = {all: pairs.slice(), rangeIds, owner: data.owner};
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
