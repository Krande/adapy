import type {FeaManifestField, FeaScalarRange} from "@/services/viewerApi";
import type {ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {getColormap} from "./colormaps";

// Colouring the solid beams by a NODAL field.
//
// The loader used to switch vertex colours off here, on the reasoning that a
// beam-solid vertex is not an FEA node so there is no value to give it. That is
// true of the vertex and false of the beam: the AFBV sidecar already names, per
// vertex, the two end nodes of its parent beam and the axial parameter between
// them — which is exactly the interpolation `installBeamSolidWarp` uses to move
// the same vertex. A quantity that can be interpolated to a position can be
// interpolated to a colour.
//
// So a displacement field paints the beams as well as the shells, which is what
// the reference postprocessor does, and what an element field (G-FORCE) already
// did here. Leaving the beams in their base material said something untrue:
// not "no data", but "zero".
//
// Linear along the beam only. The section is painted with its axial value and
// nothing is interpolated ACROSS it, because the nodal field carries no
// through-section variation — inventing one would draw a gradient the solver
// never computed.

function pickRange(field: FeaManifestField, reduction: string): [number, number] {
    const range: FeaScalarRange = field.scalar_range;
    const r = range[reduction];
    if (r) return [r[0], r[1]];
    if (field.components.length > 0) {
        const fallback = range[field.components[0]];
        if (fallback) return [fallback[0], fallback[1]];
    }
    return [0, 1];
}

/**
 * One scalar per FEA node, reduced the same way the main mesh reduces it.
 *
 * `magnitude` is the norm of the first three components — matching
 * `applyFieldToMesh`, so a beam and the shell it is welded to cannot end up
 * reading the same field on two different definitions.
 */
export function nodalScalars(
    field: FeaManifestField,
    stepValues: Float32Array,
    reduction: string,
    nPoints: number,
): Float32Array {
    const nc = field.components.length;
    const out = new Float32Array(nPoints);
    const isMagnitude = reduction === "magnitude";
    const compIdx = field.components.indexOf(reduction);
    for (let v = 0; v < nPoints; v++) {
        const s = v * nc;
        if (isMagnitude) {
            const x = stepValues[s] || 0;
            const y = nc >= 2 ? stepValues[s + 1] || 0 : 0;
            const z = nc >= 3 ? stepValues[s + 2] || 0 : 0;
            out[v] = Math.sqrt(x * x + y * y + z * z);
        } else {
            out[v] = stepValues[s + (compIdx >= 0 ? compIdx : 0)] || 0;
        }
    }
    return out;
}

/**
 * Per-vertex RGB for the beam-solid mesh, in SOURCE vertex numbering.
 *
 * Returns null when the mapping does not fit the field — a warp sidecar whose
 * node indices run past the point buffer is a stale bake, and painting from it
 * would silently colour beams by whatever happened to be in memory.
 */
export function beamSolidNodalColors(
    field: FeaManifestField,
    stepValues: Float32Array,
    reduction: string,
    warp: ParsedBeamSolidsWarp,
    colormapName: string | undefined,
    nPoints: number,
): Float32Array | null {
    const nc = field.components.length;
    if (nc === 0 || stepValues.length < nPoints * nc) return null;

    const {n_verts: nVerts, node0, node1, t} = warp;
    if (node0.length < nVerts || node1.length < nVerts || t.length < nVerts) return null;

    const scalars = nodalScalars(field, stepValues, reduction, nPoints);
    const colormap = getColormap(colormapName);
    const [lo, hi] = pickRange(field, reduction);
    const span = hi - lo;
    const scale = span > 0 ? 1 / span : 0;

    const colors = new Float32Array(nVerts * 3);
    for (let v = 0; v < nVerts; v++) {
        const a = node0[v];
        const b = node1[v];
        if (a >= nPoints || b >= nPoints) return null;
        const w = t[v];
        const value = (1 - w) * scalars[a] + w * scalars[b];
        const u = isFinite(value) ? (value - lo) * scale : 0;
        colormap(u, colors, v * 3);
    }
    return colors;
}
