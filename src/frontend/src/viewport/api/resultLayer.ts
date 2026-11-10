// What a viewport draws of an FEA result: a mesh whose per-vertex colours and displacement are
// replaced step by step, and whose deformation scale changes every frame while a result plays.
//
// The numbers are computed once, outside any viewport, as plain typed arrays
// (utils/scene/fea/nodalFieldPaint.ts): a colour and an un-scaled displacement per vertex. A layer
// only draws them. The drawn position of a vertex is `base + influence * displacement`, so playing
// a mode shape or dragging the scale is one number per frame and no vertex data moves.
//
// Nothing in this module (or anything it imports at runtime) may import three.js: the
// three.js-free viewport's chunk depends on it.

/** A result mesh as plain data, in the mesh's own (source) vertex numbering. */
export interface ResultMeshData {
    /** xyz per vertex: the undeformed shape. */
    positions: Float32Array;
    /** Corner indices into `positions`: three per triangle, two per line. Null draws the vertices
     *  in order. */
    indices: Uint32Array | null;
    topology: "triangles" | "lines" | "points";
    /** Where the mesh stands in the model: a column-major 4x4, identity when absent. The
     *  displacement is in the mesh's own axes, as the positions are. */
    matrix?: ArrayLike<number> | null;
    /** One colour for the whole layer (linear RGB in 0..1) until `setColors` paints it; a line
     *  layer that is never painted (element edges) keeps it. */
    color?: readonly [number, number, number];
    /** Lines: their width, points: their diameter, in CSS pixels. One pixel (lines) and a small
     *  dot (points) when absent. */
    sizePx?: number;
    /** How opaque the layer is, 0..1; fully opaque when absent. */
    opacity?: number;
}

/** One mesh of a shown result. Each call replaces what the previous one of its kind set; the arrays
 *  handed in are read during the call and not kept, so a caller reuses its buffers across steps. */
export interface ResultMeshLayer {
    /** Vertices of the mesh: every array below holds three floats for each. */
    readonly vertexCount: number;
    /** RGB per vertex, each channel in 0..1 (linear). */
    setColors(rgb: Float32Array): void;
    /** The step's displacement, xyz per vertex, not scaled. */
    setDisplacement(xyz: Float32Array): void;
    /** How much of the displacement is drawn: the deformation scale times the playback factor. */
    setInfluence(influence: number): void;
    /** Draw the painted colours (true, the state after `setColors`) or the layer's plain colour. */
    setColorsVisible(visible: boolean): void;
    setVisible(visible: boolean): void;
    /** Take the mesh off the viewport. The layer does nothing afterwards. */
    remove(): void;
}

/** The result-drawing part of a viewport (`ViewportApi.results`). */
export interface ViewportResults {
    /** Draw `data` as a result mesh. With `replaceModel` it becomes what the viewport shows: the
     *  loaded model goes, and the camera frames the mesh. */
    createResultMesh(data: ResultMeshData, opts?: {replaceModel?: boolean}): ResultMeshLayer;
}

/** The diagonal of the box around `positions` (xyz per vertex): the size a deformation scale is
 *  chosen against. 0 for an empty mesh. */
export function positionsDiagonal(positions: Float32Array): number {
    if (positions.length < 3) return 0;
    let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
    for (let i = 0; i + 2 < positions.length; i += 3) {
        const x = positions[i], y = positions[i + 1], z = positions[i + 2];
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
        if (z < z0) z0 = z;
        if (z > z1) z1 = z;
    }
    return Math.hypot(x1 - x0, y1 - y0, z1 - z0);
}
