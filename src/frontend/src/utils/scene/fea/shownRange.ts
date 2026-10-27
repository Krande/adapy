// The automatic colour range: the values on screen, not the values in the file.
//
// The manifest's ``scalar_range`` is the field's extremes over every step and every
// element, hidden ones included. Coloured against it, a step whose values are a
// fraction of the worst one paints one colour, and so does a model whose extremes sit
// in a handful of elements the user has hidden (GeniE's dummy-mass beams, an isolated
// group's complement). The scale is meant to describe the picture beside it, so it is
// measured over what the picture shows: this step, the drawn elements only.
//
// "Drawn" is per rendering, the same rule ``syncFeaOverlayVisibility`` applies:
//
//   * a face of the result mesh, unless its draw range is hidden;
//   * a beam solid, while the solids are on and its range is not hidden;
//   * a coloured beam line, while the solids are off, inside the line isolation and
//     not excluded (resultLineShown).
//
// Pure over its inputs apart from reading those meshes' visibility state, so the
// painters and the legend can share one answer for one paint.

import * as THREE from "three";

import type {ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {sourceVertexIndices} from "./elementLocalGeometry";
import {resultLineShown} from "./resultLineSegments";

type DrawRangeMesh = THREE.Mesh & {
    drawRanges?: Map<string, [number, number]>;
    getHiddenRanges?: () => ReadonlySet<string>;
};

/** The surfaces a result is painted on, as the session holds them. */
export interface ShownSurfaces {
    mesh: THREE.Mesh;
    /** Source (FEA node) vertex count of ``mesh``. */
    nPoints: number;
    beamSolidMesh?: THREE.Mesh | null;
    beamSolidWarp?: ParsedBeamSolidsWarp | null;
}

/** A running min/max; null until a finite value arrives. */
export class RangeAccumulator {
    private lo = Infinity;
    private hi = -Infinity;

    add(value: number): void {
        if (!Number.isFinite(value)) return;
        if (value < this.lo) this.lo = value;
        if (value > this.hi) this.hi = value;
    }

    /** The range, widened to a non-empty one around a constant field so the colour
     *  sample stays defined; null when nothing was added. */
    result(): [number, number] | null {
        if (this.lo > this.hi) return null;
        if (this.lo === this.hi) {
            const pad = Math.abs(this.lo) > 0 ? Math.abs(this.lo) * 1e-6 : 1e-12;
            return [this.lo - pad, this.hi + pad];
        }
        return [this.lo, this.hi];
    }
}

/** ``[start, count]`` (index-buffer units) of every face range of ``mesh`` that is drawn. */
function drawnFaceRanges(mesh: DrawRangeMesh): Array<[number, number]> | null {
    if (!mesh.drawRanges) return null;
    const hidden = mesh.getHiddenRanges?.() ?? new Set<string>();
    const out: Array<[number, number]> = [];
    for (const [id, [start, count]] of mesh.drawRanges) {
        if (count > 0 && !hidden.has(id)) out.push([start, count]);
    }
    return out;
}

function beamSolidsOn(surfaces: ShownSurfaces): DrawRangeMesh | null {
    const solids = surfaces.beamSolidMesh as DrawRangeMesh | null | undefined;
    return solids && solids.visible ? solids : null;
}

/** Is element ``E{label}`` drawn on any of ``surfaces``? */
export function elementShownPredicate(surfaces: ShownSurfaces): (label: number) => boolean {
    const main = surfaces.mesh as DrawRangeMesh;
    const mainHidden = main.getHiddenRanges?.() ?? new Set<string>();
    const solids = beamSolidsOn(surfaces);
    const solidHidden = solids?.getHiddenRanges?.() ?? new Set<string>();
    return (label) => {
        const id = `E${label}`;
        const face = main.drawRanges?.get(id);
        if (face && face[1] > 0) return !mainHidden.has(id);
        if (solids) {
            const solid = solids.drawRanges?.get(id);
            // With the solids on, a beam without one is drawn by nothing coloured.
            return !!solid && solid[1] > 0 && !solidHidden.has(id);
        }
        return resultLineShown(main, id);
    };
}

/** The range of a nodal field's per-node ``scalars`` over the nodes that are drawn in
 *  colour: the vertices of drawn faces, and the end nodes of drawn beam solids (whose
 *  colour is interpolated between them). Null when nothing coloured is drawn. */
export function shownNodalRange(scalars: Float32Array, surfaces: ShownSurfaces): [number, number] | null {
    const {mesh, nPoints} = surfaces;
    const shown = new Uint8Array(nPoints);

    const faces = drawnFaceRanges(mesh as DrawRangeMesh);
    const index = mesh.geometry.getIndex()?.array as ArrayLike<number> | undefined;
    if (faces && index) {
        const renderToSource = sourceVertexIndices(mesh.geometry, nPoints);
        for (const [start, count] of faces) {
            for (let i = start; i < start + count; i++) shown[renderToSource[index[i]]] = 1;
        }
    } else {
        // No per-element ranges to hide by: every node is on screen.
        shown.fill(1);
    }

    const solids = beamSolidsOn(surfaces);
    const warp = surfaces.beamSolidWarp;
    const solidIndex = solids?.geometry.getIndex()?.array as ArrayLike<number> | undefined;
    const solidFaces = solids ? drawnFaceRanges(solids) : null;
    if (solids && warp && solidIndex && solidFaces) {
        const renderToSource = sourceVertexIndices(solids.geometry, warp.n_verts);
        for (const [start, count] of solidFaces) {
            for (let i = start; i < start + count; i++) {
                const v = renderToSource[solidIndex[i]];
                const a = warp.node0[v];
                const b = warp.node1[v];
                if (a < nPoints) shown[a] = 1;
                if (b < nPoints) shown[b] = 1;
            }
        }
    }

    const acc = new RangeAccumulator();
    for (let v = 0; v < nPoints; v++) if (shown[v]) acc.add(scalars[v]);
    return acc.result();
}
