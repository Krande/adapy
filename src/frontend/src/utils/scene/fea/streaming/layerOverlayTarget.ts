// FEA streaming: a result drawn through result layers as what overlays are drawn on.
//
// Owns: the layer session's side of `../elementSource.ts` -- its meshes (the faces, the beam
// sections) as plain element geometry at the deformation shown, element colours painted over
// them as one more result layer that moves with the nodes, and hiding or isolating elements
// through the viewport, whose objects the elements are.
//
// No three.js in this module or in anything it imports.

import type {ResultMeshLayer, ViewportResults} from "@/viewport/api/resultLayer";
import {activeViewport, type ObjectRef} from "@/viewport/api/viewportApi";
import {elementKey, elementLabel, type ElementGeometry, type ElementPaint, type FeaOverlayTarget} from "../elementSource";

/** One mesh of the session: its vertices, the index list and each element's range of it. */
export interface LayerMeshView {
    positions: Float32Array;
    indices: ArrayLike<number>;
    ranges: ReadonlyMap<string, readonly [number, number]>;
    /** The displacement drawn now, xyz per vertex and not scaled; null while there is none. */
    displacement(): Float32Array | null;
}

/** What the target needs of the session that owns it. */
export interface LayerOverlayHost {
    results: ViewportResults;
    /** Where the result's meshes stand in the world. */
    matrix: ArrayLike<number>;
    /** The key the elements are known by as objects of the viewport, and their ids there; null
     *  when they are not objects. */
    model: string | null;
    ids: readonly string[] | null;
    /** The faces, or the beam sections while they are drawn; null when there are none. */
    mesh(on: "model" | "beamSolids"): LayerMeshView | null;
    /** How much of the displacement is drawn. */
    scale(): number;
    /** Goes up each time a displacement is written. */
    serial(): number;
    /** Make `layer` (a vertex per entry of `toSource`, each a vertex of the mesh `on`) part of the
     *  result: it takes the displacement and the scale from now on. Returns the function that
     *  takes it off the viewport. */
    pin(layer: ResultMeshLayer, toSource: Uint32Array, on: "model" | "beamSolids"): () => void;
}

function geometryOf(host: LayerOverlayHost, on: "model" | "beamSolids"): ElementGeometry | null {
    const mesh = host.mesh(on);
    if (!mesh) return null;
    const {positions, indices, ranges} = mesh;
    let serial = 1, seenScale = NaN, seenWrite = -1;
    return {
        ranges,
        index: indices,
        rest: (v, out, at) => {
            const i = v * 3;
            out[at] = positions[i];
            out[at + 1] = positions[i + 1];
            out[at + 2] = positions[i + 2];
        },
        position: (v, out, at) => {
            const i = v * 3;
            const moved = mesh.displacement();
            const k = moved ? host.scale() : 0;
            out[at] = positions[i] + (moved ? k * moved[i] : 0);
            out[at + 1] = positions[i + 1] + (moved ? k * moved[i + 1] : 0);
            out[at + 2] = positions[i + 2] + (moved ? k * moved[i + 2] : 0);
        },
        version: () => {
            const scale = host.scale(), write = host.serial();
            if (scale !== seenScale || write !== seenWrite) {
                seenScale = scale;
                seenWrite = write;
                serial++;
            }
            return serial;
        },
        matrix: () => host.matrix,
    };
}

/** Colours over elements: only the painted elements' triangles, a vertex per corner so each
 *  element carries its own colour, as a layer that moves with the nodes. */
function paint(host: LayerOverlayHost, spec: ElementPaint): {remove(): void} | null {
    const mesh = host.mesh(spec.on);
    if (!mesh) return null;
    const {positions, indices, ranges} = mesh;
    const c = new Float32Array(3);
    let corners = 0;
    for (const [id, range] of ranges) {
        const label = elementLabel(id);
        if (label >= 0 && spec.colorOf(label, c)) corners += Math.max(0, Math.min(range[0] + range[1], indices.length) - range[0]);
    }
    if (corners === 0) return {remove: () => {}};
    const toSource = new Uint32Array(corners);
    const local = new Float32Array(corners * 3);
    const colors = new Float32Array(corners * 3);
    let at = 0;
    for (const [id, range] of ranges) {
        const label = elementLabel(id);
        if (label < 0 || !spec.colorOf(label, c)) continue;
        const end = Math.min(range[0] + range[1], indices.length);
        for (let i = range[0]; i < end; i++, at++) {
            const v = indices[i], o = at * 3, s = v * 3;
            toSource[at] = v;
            local[o] = positions[s];
            local[o + 1] = positions[s + 1];
            local[o + 2] = positions[s + 2];
            colors[o] = c[0];
            colors[o + 1] = c[1];
            colors[o + 2] = c[2];
        }
    }
    const layer = host.results.createResultMesh({positions: local, indices: null, topology: "triangles", matrix: host.matrix, overlay: {}});
    layer.setColors(colors);
    return {remove: host.pin(layer, toSource, spec.on)};
}

/** The elements with `labels` as objects of the viewport: one ref, given new values for each. */
function* refsOf(model: string, labels: Iterable<number>): Generator<ObjectRef> {
    const ref: ObjectRef = {model, id: ""};
    for (const label of labels) {
        ref.id = elementKey(label);
        yield ref;
    }
}

export function makeLayerOverlayTarget(host: LayerOverlayHost): FeaOverlayTarget {
    const geometry: {model?: ElementGeometry | null; beamSolids?: {of: LayerMeshView; geometry: ElementGeometry}} = {};
    return {
        token: host,
        matrix: () => host.matrix,
        elements: (on) => {
            if (on === "model") return (geometry.model ??= geometryOf(host, "model"));
            const mesh = host.mesh("beamSolids");
            if (!mesh) return null;
            if (geometry.beamSolids?.of !== mesh) geometry.beamSolids = {of: mesh, geometry: geometryOf(host, "beamSolids")!};
            return geometry.beamSolids.geometry;
        },
        paint: (spec) => paint(host, spec),
        isolate: (labels) => {
            const viewport = activeViewport();
            if (!viewport || host.model === null) return;
            if (labels == null) viewport.showAll();
            else viewport.isolate(refsOf(host.model, labels));
        },
        hide: (labels) => {
            if (host.model !== null) activeViewport()?.hide(refsOf(host.model, labels));
        },
        hidden: () => {
            const out: number[] = [];
            const visibility = activeViewport()?.objectVisibility();
            if (!visibility || !host.ids || host.model === null) return out;
            for (const id of host.ids) {
                if (visibility(host.model, id) !== "hidden") continue;
                const label = elementLabel(id);
                if (label >= 0) out.push(label);
            }
            return out;
        },
    };
}
