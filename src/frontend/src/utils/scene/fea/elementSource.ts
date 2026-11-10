// The FEA result on screen, as far as something drawn over it needs it: its elements, where their
// vertices are now, and the element-level things only the result itself can do (paint, hide,
// isolate). One lookup, `activeFeaTarget`, whichever way the result is drawn: a three.js mesh
// (./meshOverlayTarget.ts) or result layers (./streaming/layerOverlayTarget.ts). Each registers
// itself here when its loader is.
//
// No three.js in this module or in anything it imports.

/** One mesh of a result as plain data: the faces of the model, or its beam sections. */
export interface ElementGeometry {
    /** Each element's range of `index` (start, count), by its key ("E123"). */
    ranges: ReadonlyMap<string, readonly [number, number]>;
    /** The mesh's index list: three vertices per triangle. */
    index: ArrayLike<number>;
    /** Write a vertex's undeformed position, in the mesh's own axes, to `out[at .. at + 2]`. */
    rest(vertex: number, out: Float32Array, at: number): void;
    /** The same where the vertex is drawn now: at the deformation shown. */
    position(vertex: number, out: Float32Array, at: number): void;
    /** A number that is another one whenever `position` may answer differently. */
    version(): number;
    /** Where the mesh stands in the world: a column-major 4x4. */
    matrix(): ArrayLike<number>;
}

/** Colours for elements, drawn over the result and moving with it. */
export interface ElementPaint {
    /** Which mesh: the model's faces, or its beam sections while they are shown. */
    on: "model" | "beamSolids";
    /** The colour (linear RGB into `out`) of the element with this label; false leaves it unpainted. */
    colorOf(label: number, out: Float32Array): boolean;
    /** A name for what is drawn, for whoever inspects the scene. */
    name: string;
}

export interface FeaOverlayTarget {
    /** A different object for a different result: what was pinned to another one is stale. */
    readonly token: object;
    /** Where the result's own coordinates stand in the world: a column-major 4x4. */
    matrix(): ArrayLike<number>;
    /** Null for the beam sections while they are not drawn, and for a mesh without faces. */
    elements(on: "model" | "beamSolids"): ElementGeometry | null;
    /** Null when the mesh asked for is not drawn. */
    paint(paint: ElementPaint): {remove(): void} | null;
    /** Show only the elements with these labels; null shows all again. */
    isolate(labels: Iterable<number> | null): void;
    /** Hide them, on top of what is hidden already. */
    hide(labels: Iterable<number>): void;
    /** The labels of the hidden elements. */
    hidden(): number[];
}

const lookups: (() => FeaOverlayTarget | null)[] = [];

/** A way a result is drawn offers itself as a target: asked each time, as its result comes and goes. */
export function registerFeaTarget(lookup: () => FeaOverlayTarget | null): void {
    lookups.push(lookup);
}

/** The FEA result on screen, or null when none is loaded. */
export function activeFeaTarget(): FeaOverlayTarget | null {
    for (const lookup of lookups) {
        const target = lookup();
        if (target) return target;
    }
    return null;
}

const gone = new Set<(token: object) => void>();

/** Called when a result leaves the screen, with its target's token. */
export function onFeaTargetGone(listener: (token: object) => void): () => void {
    gone.add(listener);
    return () => void gone.delete(listener);
}

export function notifyFeaTargetGone(token: object): void {
    for (const listener of gone) listener(token);
}

/** An element's key from its label. */
export const elementKey = (label: number): string => `E${label}`;

/** The label of an element key ("E123" -> 123), or -1 when it is not one. */
export function elementLabel(key: string): number {
    if (key.charCodeAt(0) !== 69 /* E */ || key.length < 2) return -1;
    let label = 0;
    for (let i = 1; i < key.length; i++) {
        const digit = key.charCodeAt(i) - 48;
        if (digit < 0 || digit > 9) return -1;
        label = label * 10 + digit;
    }
    return label;
}

/** The index-list vertices of `labels`' faces, three per triangle; each element once. */
export function elementVertices(geometry: ElementGeometry, labels: Iterable<number>): number[] {
    const {index, ranges} = geometry;
    const out: number[] = [];
    const seen = new Set<number>();
    for (const label of labels) {
        if (seen.has(label)) continue;
        seen.add(label);
        const range = ranges.get(elementKey(label));
        if (!range) continue;
        const end = Math.min(range[0] + range[1], index.length);
        for (let i = range[0]; i < end; i++) out.push(index[i]);
    }
    return out;
}

/** The edges of a triangle list that only one of its triangles has: its outline, as vertex pairs.
 *  Vertices are matched by position (a mesh may split them per element), so two elements' shared
 *  edge drops out. `rest` writes a vertex's position to `out[at .. at + 2]`. */
export function boundaryEdges(
    rest: (vertex: number, out: Float32Array, at: number) => void,
    tris: readonly number[],
    tolerance = 1e-5,
): number[] {
    const p = new Float32Array(3);
    const keys = new Map<number, string>();
    const key = (v: number): string => {
        let k = keys.get(v);
        if (k === undefined) {
            rest(v, p, 0);
            k = `${Math.round(p[0] / tolerance)},${Math.round(p[1] / tolerance)},${Math.round(p[2] / tolerance)}`;
            keys.set(v, k);
        }
        return k;
    };
    const edges = new Map<string, {count: number; a: number; b: number}>();
    for (let t = 0; t + 2 < tris.length; t += 3) {
        for (let e = 0; e < 3; e++) {
            const a = tris[t + e];
            const b = tris[t + ((e + 1) % 3)];
            const ka = key(a);
            const kb = key(b);
            if (ka === kb) continue; // a collapsed triangle
            const k = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
            const hit = edges.get(k);
            if (hit) hit.count++;
            else edges.set(k, {count: 1, a, b});
        }
    }
    const out: number[] = [];
    for (const {count, a, b} of edges.values()) if (count === 1) out.push(a, b);
    return out;
}

/** `vertices` where they are drawn now, xyz each in the mesh's own axes, into `out`. */
export function placeVertices(geometry: ElementGeometry, vertices: readonly number[], out: Float32Array): Float32Array {
    for (let i = 0; i < vertices.length; i++) geometry.position(vertices[i], out, i * 3);
    return out;
}

/** The point `(x, y, z)` of a mesh standing at `m` (column-major 4x4), in the world, into `out`. */
export function toWorld(m: ArrayLike<number>, x: number, y: number, z: number, out: number[] | Float32Array, at = 0): void {
    out[at] = m[0] * x + m[4] * y + m[8] * z + m[12];
    out[at + 1] = m[1] * x + m[5] * y + m[9] * z + m[13];
    out[at + 2] = m[2] * x + m[6] * y + m[10] * z + m[14];
}
