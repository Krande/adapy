// The three.js loader's FEA result as what overlays are drawn on (./elementSource.ts): the result
// mesh and its beam-solid mesh as plain element geometry, and the element-level verbs on the mesh
// itself (paint, hide, isolate), which stay what they were before the lookup was shared.

import * as THREE from "three";

import {useModelSessionStore} from "@/state/modelSession";
import {requestRender} from "@/state/perfStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {
    elementKey,
    elementLabel,
    notifyFeaTargetGone,
    registerFeaTarget,
    type ElementGeometry,
    type ElementPaint,
    type FeaOverlayTarget,
} from "./elementSource";

/** The FEA mesh's structural surface used here (core's CustomBatchedMesh). */
interface FeaMesh extends THREE.Mesh {
    drawRanges?: Map<string, [number, number]>;
    hideBatchDrawRange?: (ids: Iterable<string>) => void;
    isolateDrawRanges?: (keep: ReadonlySet<string>) => void;
    unhideAllDrawRanges?: () => void;
    getHiddenRanges?: () => ReadonlySet<string>;
}

const NO_RANGES: ReadonlyMap<string, readonly [number, number]> = new Map();
const NO_INDEX: ArrayLike<number> = [];
const NO_WEIGHTS: readonly number[] = [];

/** `mesh` as element geometry. Everything is read from the mesh when asked: a paint may replace
 *  its attributes. */
function meshGeometry(mesh: FeaMesh): ElementGeometry {
    // What the drawn surface depends on: the weights AND the morph data, which a step change
    // rewrites in place (same attribute, bumped version).
    let serial = 1;
    let seenMorph: THREE.BufferAttribute | undefined;
    let seenMorphVersion = -1;
    const seenWeights: number[] = [];
    const morphs = () => mesh.geometry.morphAttributes.position as THREE.BufferAttribute[] | undefined;
    return {
        get ranges() {
            return mesh.drawRanges ?? NO_RANGES;
        },
        get index() {
            return mesh.geometry.getIndex()?.array ?? NO_INDEX;
        },
        rest: (v, out, at) => {
            const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
            out[at] = pos.getX(v);
            out[at + 1] = pos.getY(v);
            out[at + 2] = pos.getZ(v);
        },
        // Where a vertex is drawn now: its position plus the morph (warp) at the current weights.
        position: (v, out, at) => {
            const geom = mesh.geometry;
            const pos = geom.getAttribute("position") as THREE.BufferAttribute;
            const px = pos.getX(v), py = pos.getY(v), pz = pos.getZ(v);
            out[at] = px;
            out[at + 1] = py;
            out[at + 2] = pz;
            const targets = morphs();
            const weights = mesh.morphTargetInfluences;
            if (!targets?.length || !weights) return;
            let sum = 0, x = 0, y = 0, z = 0;
            for (let m = 0; m < targets.length; m++) {
                const w = weights[m] ?? 0;
                if (!w) continue;
                sum += w;
                x += w * targets[m].getX(v);
                y += w * targets[m].getY(v);
                z += w * targets[m].getZ(v);
            }
            if (geom.morphTargetsRelative) {
                out[at] = px + x;
                out[at + 1] = py + y;
                out[at + 2] = pz + z;
            } else if (sum) {
                out[at] = px * (1 - sum) + x;
                out[at + 1] = py * (1 - sum) + y;
                out[at + 2] = pz * (1 - sum) + z;
            }
        },
        version: () => {
            const morph = morphs()?.[0];
            const weights = mesh.morphTargetInfluences ?? NO_WEIGHTS;
            const morphVersion = morph?.version ?? 0;
            let changed = morph !== seenMorph || morphVersion !== seenMorphVersion || weights.length !== seenWeights.length;
            seenMorph = morph;
            seenMorphVersion = morphVersion;
            seenWeights.length = weights.length;
            for (let i = 0; i < weights.length; i++) {
                if (seenWeights[i] !== weights[i]) {
                    seenWeights[i] = weights[i];
                    changed = true;
                }
            }
            if (changed) serial++;
            return serial;
        },
        matrix: () => {
            mesh.updateWorldMatrix(true, false);
            return mesh.matrixWorld.elements;
        },
    };
}

/** Colour elements of `mesh`, over it: a non-indexed copy, so each element's triangles carry their
 *  own colour, with the mesh's morph, so the paint follows the warp. */
function paintMesh(root: FeaMesh, mesh: FeaMesh, paint: ElementPaint): {remove(): void} | null {
    const index = mesh.geometry.getIndex();
    const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!index || !pos || !mesh.drawRanges) return null;

    const idx = index.array;
    const n = idx.length;
    const positions = new Float32Array(n * 3);
    for (let i = 0, o = 0; i < n; i++, o += 3) {
        const v = idx[i];
        positions[o] = pos.getX(v);
        positions[o + 1] = pos.getY(v);
        positions[o + 2] = pos.getZ(v);
    }
    const colors = new Float32Array(n * 3);
    const keep = new Uint8Array(n);
    const c = new Float32Array(3);
    for (const [id, [start, count]] of mesh.drawRanges) {
        const label = elementLabel(id);
        if (label < 0 || !paint.colorOf(label, c)) continue;
        for (let i = start, o = start * 3; i < start + count && i < n; i++, o += 3) {
            keep[i] = 1;
            colors[o] = c[0];
            colors[o + 1] = c[1];
            colors[o + 2] = c[2];
        }
    }
    // Triangles not painted collapse to a point (cheaper than a compacted copy, and the morph copy below
    // keeps one layout).
    const collapse = (a: Float32Array) => {
        for (let k = 0; k + 2 < n; k += 3) {
            if (keep[k]) continue;
            a.copyWithin((k + 1) * 3, k * 3, k * 3 + 3);
            a.copyWithin((k + 2) * 3, k * 3, k * 3 + 3);
        }
    };
    collapse(positions);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    // The model's deformation, so the paint follows the warp.
    const morphs = mesh.geometry.morphAttributes.position as THREE.BufferAttribute[] | undefined;
    if (morphs?.length) {
        geom.morphAttributes.position = morphs.map((attr) => {
            const out = new Float32Array(n * 3);
            for (let i = 0, o = 0; i < n; i++, o += 3) {
                const v = idx[i];
                out[o] = attr.getX(v);
                out[o + 1] = attr.getY(v);
                out[o + 2] = attr.getZ(v);
            }
            collapse(out);
            return new THREE.BufferAttribute(out, 3);
        });
        geom.morphTargetsRelative = mesh.geometry.morphTargetsRelative;
    }
    const material = new THREE.MeshBasicMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -16,
    });
    const overlay = new THREE.Mesh(geom, material);
    overlay.name = paint.name;
    overlay.renderOrder = 2;
    // Shared, not copied: the warp slider writes the mesh's influences and the paint moves with them.
    overlay.morphTargetInfluences = mesh.morphTargetInfluences;
    overlay.morphTargetDictionary = mesh.morphTargetDictionary;
    overlay.raycast = () => undefined; // never stands in front of the object a click is meant for
    overlay.layers.mask = root.layers.mask;
    // On the mesh it paints: the beam solids hang under the FEA mesh in their own frame.
    mesh.add(overlay);
    clipWithModel(overlay);
    requestRender();
    return {
        remove: () => {
            overlay.removeFromParent();
            geom.dispose();
            material.dispose();
            requestRender();
        },
    };
}

const targets = new WeakMap<THREE.Mesh, FeaOverlayTarget>();

function targetOf(mesh: FeaMesh): FeaOverlayTarget {
    let target = targets.get(mesh);
    if (target) return target;
    const model = meshGeometry(mesh);
    let solidsOf: FeaMesh | null = null;
    let solids: ElementGeometry | null = null;
    /** The beam-solid mesh while it is shown (same `E<label>` draw ranges as the FEA mesh). */
    const beamSolidMesh = (): FeaMesh | null => {
        const fea = useModelSessionStore.getState().current()?.fea;
        const shown = fea?.mesh === mesh ? (fea.beamSolidMesh as FeaMesh | undefined) : undefined;
        return shown?.visible ? shown : null;
    };
    target = {
        token: mesh,
        matrix: model.matrix,
        elements: (on) => {
            if (on === "model") return model;
            const shown = beamSolidMesh();
            if (!shown) return null;
            if (shown !== solidsOf) {
                solidsOf = shown;
                solids = meshGeometry(shown);
            }
            return solids;
        },
        paint: (paint) => {
            const on = paint.on === "beamSolids" ? beamSolidMesh() : mesh;
            return on ? paintMesh(mesh, on, paint) : null;
        },
        isolate: (labels) => {
            if (labels == null) {
                mesh.unhideAllDrawRanges?.();
            } else {
                const keep = new Set<string>();
                for (const label of labels) keep.add(elementKey(label));
                mesh.isolateDrawRanges?.(keep);
            }
            requestRender();
        },
        hide: (labels) => {
            const ids: string[] = [];
            for (const label of labels) ids.push(elementKey(label));
            mesh.hideBatchDrawRange?.(ids);
            requestRender();
        },
        hidden: () => {
            const out: number[] = [];
            for (const id of mesh.getHiddenRanges?.() ?? []) {
                const label = elementLabel(id);
                if (label >= 0) out.push(label);
            }
            return out;
        },
    };
    targets.set(mesh, target);
    return target;
}

registerFeaTarget(() => {
    const mesh = useModelSessionStore.getState().current()?.fea?.mesh as FeaMesh | undefined;
    return mesh ? targetOf(mesh) : null;
});

// A result that left the screen takes what was pinned to it along. The loader's handle is set on
// the open session without a store update, so the loader says when it did (`feaMeshChanged`); a
// session that closes or opens is seen here.
let shown: THREE.Mesh | null = null;

/** The FEA mesh of the open session may be another one now. */
export function feaMeshChanged(): void {
    const now = useModelSessionStore.getState().current()?.fea?.mesh ?? null;
    if (now === shown) return;
    const before = shown;
    shown = now;
    if (before) notifyFeaTargetGone(before);
}

useModelSessionStore.subscribe(feaMeshChanged);
