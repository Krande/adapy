// The beam-solid warp: each vertex of a beam's solid section moved by its beam's node displacements
// and section rotations. No three.js: the WebGPU embed uses it as is, and streaming/warp.ts
// re-exports it for the three.js viewer.
import type {ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import type {FeaManifestField} from "@/services/viewerApi";
import {rotationOffsets, translationOffsets, warpValue} from "./warpComponents";

/** The beam-solid morph delta, per vertex of the solid (in the warp sidecar's
 *  vertex numbering).
 *
 *    delta_v = lerp(u[node0], u[node1], t) + rot(lerp(θ[node0], θ[node1], t), r_v)
 *    r_v     = v_base − lerp(P[node0], P[node1], t)
 *
 *  The first term moves the section with its beam axis. The second turns it.
 *  Without it the section only translates: its end planes stay parallel to where
 *  they started, so a boom that swings 45° drags undeformed-plane sections along
 *  (and a torsion mode, whose axis does not translate, does not move at all).
 *
 *  θ per node comes from the field's rotations when it names them (see
 *  rotationOffsets). Otherwise -- an Abaqus `U`, which carries U1..U3 only, the
 *  rotations being a separate UR output -- it is the rotation of the beam axis
 *  itself: each beam's chord turned from its undeformed to its deformed
 *  direction, averaged over the beams that meet at the node so neighbouring
 *  sections stay joined. That misses twist about the axis, which only a rotation
 *  field can tell.
 *
 *  `rot` is the exact (Rodrigues) rotation for a transient or static step, where
 *  a crane boom really does turn tens of degrees and the small-rotation
 *  θ × r both under-turns and swells the section (by 28 % at 45°). Mode shapes
 *  keep θ × r: the delta must stay linear in the amplitude, since the oscillation
 *  drives it with a signed influence.
 *
 *  Needs the undeformed node positions `nodePositions`; without them the delta is
 *  translation only. Zero everywhere when there is no warp source. */
export function beamSolidDisplacement(
    warp: ParsedBeamSolidsWarp,
    basePositions: Float32Array,
    warpField: FeaManifestField | undefined,
    warpStepValues: Float32Array | undefined,
    nodePositions?: Float32Array,
): Float32Array {
    const nVerts = warp.n_verts;
    const displacement = new Float32Array(nVerts * 3);
    // No warp source means no deformation, which is what the user gets when they
    // pick a reaction field or turn warp off.
    if (!warpField || !warpStepValues) return displacement;

    const nc = warpField.components.length;
    // WHICH slots hold the translation. A Sesam displacement field is
    // ["ALL","X","Y","Z","RX","RY","RZ"] -- reading slots 0..2 warps every
    // vertex by (ALL, X, Y), and since `ALL` is a non-negative aggregate the
    // beams visibly fly off. See translationOffsets.
    const axes = translationOffsets(warpField);
    const finite = warpField.analysis_kind === "transient" || warpField.analysis_kind === "static";
    const theta = nodePositions
        ? nodeRotations(warp, warpField, warpStepValues, nodePositions, axes, finite)
        : null;
    // Finite: one rotation matrix per node, blended per vertex -- the trig once
    // per node rather than once per vertex of the solid.
    const mats = theta && finite ? rotationMatrices(theta) : null;
    const n0 = warp.node0;
    const n1 = warp.node1;
    const ts = warp.t;
    for (let v = 0; v < nVerts; v++) {
        const t = ts[v];
        const omt = 1 - t;
        const a = n0[v] * nc;
        const b = n1[v] * nc;
        const out = v * 3;
        displacement[out + 0] = omt * warpValue(warpStepValues, a, axes[0]) + t * warpValue(warpStepValues, b, axes[0]);
        displacement[out + 1] = omt * warpValue(warpStepValues, a, axes[1]) + t * warpValue(warpStepValues, b, axes[1]);
        displacement[out + 2] = omt * warpValue(warpStepValues, a, axes[2]) + t * warpValue(warpStepValues, b, axes[2]);
        if (!theta || !nodePositions) continue;

        const p0 = n0[v] * 3;
        const p1 = n1[v] * 3;
        const rx = basePositions[out + 0] - (omt * nodePositions[p0 + 0] + t * nodePositions[p1 + 0]);
        const ry = basePositions[out + 1] - (omt * nodePositions[p0 + 1] + t * nodePositions[p1 + 1]);
        const rz = basePositions[out + 2] - (omt * nodePositions[p0 + 2] + t * nodePositions[p1 + 2]);
        if (mats) {
            const m0 = n0[v] * 9;
            const m1 = n1[v] * 9;
            const a0 = omt * mats[m0] + t * mats[m1] - 1;
            const a1 = omt * mats[m0 + 1] + t * mats[m1 + 1];
            const a2 = omt * mats[m0 + 2] + t * mats[m1 + 2];
            const a3 = omt * mats[m0 + 3] + t * mats[m1 + 3];
            const a4 = omt * mats[m0 + 4] + t * mats[m1 + 4] - 1;
            const a5 = omt * mats[m0 + 5] + t * mats[m1 + 5];
            const a6 = omt * mats[m0 + 6] + t * mats[m1 + 6];
            const a7 = omt * mats[m0 + 7] + t * mats[m1 + 7];
            const a8 = omt * mats[m0 + 8] + t * mats[m1 + 8] - 1;
            displacement[out + 0] += a0 * rx + a1 * ry + a2 * rz;
            displacement[out + 1] += a3 * rx + a4 * ry + a5 * rz;
            displacement[out + 2] += a6 * rx + a7 * ry + a8 * rz;
        } else {
            const tx = omt * theta[p0 + 0] + t * theta[p1 + 0];
            const ty = omt * theta[p0 + 1] + t * theta[p1 + 1];
            const tz = omt * theta[p0 + 2] + t * theta[p1 + 2];
            displacement[out + 0] += ty * rz - tz * ry;
            displacement[out + 1] += tz * rx - tx * rz;
            displacement[out + 2] += tx * ry - ty * rx;
        }
    }
    return displacement;
}

/** Row-major 3×3 rotation matrix per node from its rotation vector (Rodrigues). */
function rotationMatrices(theta: Float64Array): Float64Array {
    const n = theta.length / 3;
    const out = new Float64Array(n * 9);
    for (let i = 0; i < n; i++) {
        const tx = theta[i * 3], ty = theta[i * 3 + 1], tz = theta[i * 3 + 2];
        const o = i * 9;
        const angle = Math.hypot(tx, ty, tz);
        if (angle === 0) {
            out[o] = 1; out[o + 4] = 1; out[o + 8] = 1;
            continue;
        }
        const x = tx / angle, y = ty / angle, z = tz / angle;
        const c = Math.cos(angle), s = Math.sin(angle), k = 1 - c;
        out[o + 0] = c + x * x * k;     out[o + 1] = x * y * k - z * s; out[o + 2] = x * z * k + y * s;
        out[o + 3] = y * x * k + z * s; out[o + 4] = c + y * y * k;     out[o + 5] = y * z * k - x * s;
        out[o + 6] = z * x * k - y * s; out[o + 7] = z * y * k + x * s; out[o + 8] = c + z * z * k;
    }
    return out;
}

/** The beam graph of a warp sidecar: each node's neighbours along a beam, CSR.
 *  Vertices come in runs per beam, so only a change of pair is looked up.
 *  Cached per sidecar. */
interface BeamGraph {
    start: Uint32Array;
    neighbours: Uint32Array;
}
const graphs = new WeakMap<ParsedBeamSolidsWarp, BeamGraph>();
function beamGraph(warp: ParsedBeamSolidsWarp): BeamGraph {
    const hit = graphs.get(warp);
    if (hit) return hit;
    const seen = new Set<string>();
    const pairs: number[] = [];
    let last0 = -1;
    let last1 = -1;
    let nNodes = 0;
    for (let v = 0; v < warp.n_verts; v++) {
        const a = warp.node0[v];
        const b = warp.node1[v];
        if (a === last0 && b === last1) continue;
        last0 = a;
        last1 = b;
        if (a === b) continue;
        const key = a < b ? `${a},${b}` : `${b},${a}`;
        if (seen.has(key)) continue;
        seen.add(key);
        pairs.push(a, b);
        nNodes = Math.max(nNodes, a + 1, b + 1);
    }
    const start = new Uint32Array(nNodes + 1);
    for (const n of pairs) start[n + 1]++;
    for (let n = 0; n < nNodes; n++) start[n + 1] += start[n];
    const fill = start.slice(0, nNodes);
    const neighbours = new Uint32Array(pairs.length);
    for (let p = 0; p < pairs.length; p += 2) {
        neighbours[fill[pairs[p]]++] = pairs[p + 1];
        neighbours[fill[pairs[p + 1]]++] = pairs[p];
    }
    const graph = {start, neighbours};
    graphs.set(warp, graph);
    return graph;
}

/** Below this sine every beam at a node counts as parallel: the turn about their
 *  common axis is then unknown and left at zero. */
const PARALLEL_SIN = 0.05;

/** θ per node (xyz rotation vector, in the node numbering of `nodePositions`):
 *  the field's own rotations when it names them, else the rotation that best
 *  turns the node's undeformed beam directions onto its deformed ones. Null when
 *  there is neither. */
function nodeRotations(
    warp: ParsedBeamSolidsWarp,
    field: FeaManifestField,
    values: Float32Array,
    nodePositions: Float32Array,
    axes: [number, number, number],
    finite: boolean,
): Float64Array | null {
    const nNodes = nodePositions.length / 3;
    const nc = field.components.length;
    if (values.length < nNodes * nc) return null;
    const theta = new Float64Array(nNodes * 3);
    const rot = rotationOffsets(field);
    if (rot) {
        for (let n = 0; n < nNodes; n++) {
            const b = n * nc;
            theta[n * 3] = warpValue(values, b, rot[0]);
            theta[n * 3 + 1] = warpValue(values, b, rot[1]);
            theta[n * 3 + 2] = warpValue(values, b, rot[2]);
        }
        return theta;
    }

    const graph = beamGraph(warp);
    const nGraph = Math.min(nNodes, graph.start.length - 1);
    if (nGraph <= 0) return null;
    const fit = new BeamRotationFit();
    const out = new Float64Array(3);
    for (let n = 0; n < nGraph; n++) {
        const s = graph.start[n];
        const e = graph.start[n + 1];
        if (s === e) continue;
        fit.reset();
        const ux = warpValue(values, n * nc, axes[0]);
        const uy = warpValue(values, n * nc, axes[1]);
        const uz = warpValue(values, n * nc, axes[2]);
        for (let k = s; k < e; k++) {
            const m = graph.neighbours[k];
            if (m >= nNodes) continue;
            const dx = nodePositions[m * 3] - nodePositions[n * 3];
            const dy = nodePositions[m * 3 + 1] - nodePositions[n * 3 + 1];
            const dz = nodePositions[m * 3 + 2] - nodePositions[n * 3 + 2];
            fit.add(
                dx, dy, dz,
                warpValue(values, m * nc, axes[0]) - ux,
                warpValue(values, m * nc, axes[1]) - uy,
                warpValue(values, m * nc, axes[2]) - uz,
            );
        }
        if (finite ? fit.finite(out) : fit.linear(out)) theta.set(out, n * 3);
    }
    return theta;
}

/**
 * The rotation of a node, from the beams that meet there: each beam's direction
 * before (`r`) and after (`b`) the step.
 *
 * Finite: Wahba's problem, the rotation R minimising Σ|b − R r|², by Davenport's
 * q-method (largest eigenvector of a 4×4, by power iteration). One beam, or only
 * parallel ones, fix no turn about their axis: then it is the plain turn of the
 * (mean) beam direction.
 *
 * Linear: the small-rotation θ minimising Σ|θ × r − Δr|², from the 3×3 normal
 * equations Σ(I − r rᵀ) θ = Σ r × Δr. Linear in Δr, as a mode shape needs.
 */
export class BeamRotationFit {
    private rs: number[] = [];
    private bs: number[] = [];

    reset(): void {
        this.rs.length = 0;
        this.bs.length = 0;
    }

    /** One beam: its undeformed vector `d` and the change `u` of that vector. */
    add(dx: number, dy: number, dz: number, ux: number, uy: number, uz: number): void {
        const l = Math.hypot(dx, dy, dz);
        if (!(l > 0)) return;
        this.rs.push(dx / l, dy / l, dz / l);
        // Linear: Δr = u / l. Finite: b = (d + u) normalised -- both kept as `bs`
        // raw (d+u)/l; each method normalises as it needs.
        this.bs.push((dx + ux) / l, (dy + uy) / l, (dz + uz) / l);
    }

    private spread(): boolean {
        const r = this.rs;
        for (let i = 3; i < r.length; i += 3) {
            const cx = r[1] * r[i + 2] - r[2] * r[i + 1];
            const cy = r[2] * r[i] - r[0] * r[i + 2];
            const cz = r[0] * r[i + 1] - r[1] * r[i];
            if (Math.hypot(cx, cy, cz) > PARALLEL_SIN) return true;
        }
        return false;
    }

    linear(out: Float64Array): boolean {
        const r = this.rs;
        const b = this.bs;
        if (!r.length) return false;
        let a00 = 0, a01 = 0, a02 = 0, a11 = 0, a12 = 0, a22 = 0;
        let hx = 0, hy = 0, hz = 0;
        for (let i = 0; i < r.length; i += 3) {
            const x = r[i], y = r[i + 1], z = r[i + 2];
            const dx = b[i] - x, dy = b[i + 1] - y, dz = b[i + 2] - z;
            a00 += 1 - x * x; a01 -= x * y; a02 -= x * z;
            a11 += 1 - y * y; a12 -= y * z; a22 += 1 - z * z;
            hx += y * dz - z * dy;
            hy += z * dx - x * dz;
            hz += x * dy - y * dx;
        }
        // A tiny ridge: parallel beams leave the turn about their axis free, and
        // this pins it to zero instead of letting it blow up.
        const eps = 1e-6 * (a00 + a11 + a22);
        a00 += eps; a11 += eps; a22 += eps;
        const c00 = a11 * a22 - a12 * a12;
        const c01 = a02 * a12 - a01 * a22;
        const c02 = a01 * a12 - a02 * a11;
        const det = a00 * c00 + a01 * c01 + a02 * c02;
        if (!(Math.abs(det) > 0)) return false;
        const c11 = a00 * a22 - a02 * a02;
        const c12 = a01 * a02 - a00 * a12;
        const c22 = a00 * a11 - a01 * a01;
        out[0] = (c00 * hx + c01 * hy + c02 * hz) / det;
        out[1] = (c01 * hx + c11 * hy + c12 * hz) / det;
        out[2] = (c02 * hx + c12 * hy + c22 * hz) / det;
        return true;
    }

    finite(out: Float64Array): boolean {
        const r = this.rs;
        const b = this.bs;
        const n = r.length / 3;
        if (!n) return false;
        if (!this.spread()) {
            // The turn of the mean direction.
            let rx = 0, ry = 0, rz = 0, bx = 0, by = 0, bz = 0;
            for (let i = 0; i < r.length; i += 3) {
                // Parallel beams may point opposite ways: align them first.
                const sgn = r[i] * r[0] + r[i + 1] * r[1] + r[i + 2] * r[2] < 0 ? -1 : 1;
                rx += sgn * r[i]; ry += sgn * r[i + 1]; rz += sgn * r[i + 2];
                bx += sgn * b[i]; by += sgn * b[i + 1]; bz += sgn * b[i + 2];
            }
            return turnBetween(rx, ry, rz, bx, by, bz, out);
        }
        // B = Σ b̂ r̂ᵀ
        let b00 = 0, b01 = 0, b02 = 0, b10 = 0, b11 = 0, b12 = 0, b20 = 0, b21 = 0, b22 = 0;
        for (let i = 0; i < r.length; i += 3) {
            const l = Math.hypot(b[i], b[i + 1], b[i + 2]);
            if (!(l > 0)) continue;
            const x = b[i] / l, y = b[i + 1] / l, z = b[i + 2] / l;
            b00 += x * r[i]; b01 += x * r[i + 1]; b02 += x * r[i + 2];
            b10 += y * r[i]; b11 += y * r[i + 1]; b12 += y * r[i + 2];
            b20 += z * r[i]; b21 += z * r[i + 1]; b22 += z * r[i + 2];
        }
        const sigma = b00 + b11 + b22;
        const zx = b12 - b21, zy = b20 - b02, zz = b01 - b10;
        // K + nI: same eigenvectors, all eigenvalues ≥ 0, so power iteration
        // finds the largest.
        const k = [
            2 * b00 - sigma + n, b01 + b10, b02 + b20, zx,
            b01 + b10, 2 * b11 - sigma + n, b12 + b21, zy,
            b02 + b20, b12 + b21, 2 * b22 - sigma + n, zz,
            zx, zy, zz, sigma + n,
        ];
        let q0 = 0, q1 = 0, q2 = 0, q3 = 1;
        for (let it = 0; it < 64; it++) {
            const p0 = k[0] * q0 + k[1] * q1 + k[2] * q2 + k[3] * q3;
            const p1 = k[4] * q0 + k[5] * q1 + k[6] * q2 + k[7] * q3;
            const p2 = k[8] * q0 + k[9] * q1 + k[10] * q2 + k[11] * q3;
            const p3 = k[12] * q0 + k[13] * q1 + k[14] * q2 + k[15] * q3;
            const l = Math.hypot(p0, p1, p2, p3);
            if (!(l > 0)) return false;
            const d = Math.abs(p0 / l - q0) + Math.abs(p1 / l - q1) + Math.abs(p2 / l - q2) + Math.abs(p3 / l - q3);
            q0 = p0 / l; q1 = p1 / l; q2 = p2 / l; q3 = p3 / l;
            if (d < 1e-12) break;
        }
        // Davenport's q is the attitude (frame) quaternion; the turn that carries r
        // onto b is its inverse: axis −q_v, angle 2·acos(q₄).
        if (q3 < 0) { q0 = -q0; q1 = -q1; q2 = -q2; q3 = -q3; }
        const s = Math.hypot(q0, q1, q2);
        if (s === 0) { out[0] = 0; out[1] = 0; out[2] = 0; return true; }
        const angle = 2 * Math.atan2(s, q3);
        out[0] = (-q0 / s) * angle;
        out[1] = (-q1 / s) * angle;
        out[2] = (-q2 / s) * angle;
        return true;
    }
}

/** The rotation vector turning direction r onto direction b (shortest way). */
function turnBetween(rx: number, ry: number, rz: number, bx: number, by: number, bz: number, out: Float64Array): boolean {
    const cx = ry * bz - rz * by;
    const cy = rz * bx - rx * bz;
    const cz = rx * by - ry * bx;
    const sin = Math.hypot(cx, cy, cz);
    const cos = rx * bx + ry * by + rz * bz;
    if (!(Math.hypot(sin, cos) > 0)) return false;
    if (sin === 0) { out[0] = 0; out[1] = 0; out[2] = 0; return true; }
    const angle = Math.atan2(sin, cos);
    out[0] = (cx / sin) * angle;
    out[1] = (cy / sin) * angle;
    out[2] = (cz / sin) * angle;
    return true;
}
