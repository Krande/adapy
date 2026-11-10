// Number labels over a loaded FEA result: node ids, element ids and element values, placed in
// screen space. Plain data in and out: the result as a `LabelSource` (its elements, their
// vertices and where each vertex is now), the camera as a `ScreenProjector` (a batch of world
// points to pixels), so the same placement serves whichever viewport is on screen.
//
// Labelling every node of a million-node model is neither affordable (an HTML
// node each) nor readable, so this picks what is worth a label:
//
//   * With a selection on the result, only the selected elements (and their
//     nodes) are candidates: "which node is that" is asked about a few elements.
//   * With none, the elements are sampled to `scanCap`, so the cost of a pass is
//     bounded whatever the model's size.
//   * Candidates behind the camera or off screen are dropped, and the rest are
//     thinned on a screen grid (`cellW` x `cellH` px, one label per cell), which
//     both declutters and caps the count: zoom in and the cells hold fewer
//     candidates, so more of them get a label.
//
// Positions follow the deformed shape. Hidden elements get no label. Occlusion is not
// tested: a label is drawn for a node behind the surface in front of it.
//
// No three.js in this module or in anything it imports.

export type LabelKind = "node" | "element" | "value";

export interface ScreenLabel {
    /** Stable across passes while the same entity is labelled. */
    key: string;
    kind: LabelKind;
    text: string;
    /** CSS pixels from the view's top-left. */
    x: number;
    y: number;
}

/** A result's mesh, as far as labelling needs it. */
export interface LabelSource {
    /** Each element's range of `index` (start, count), by its key ("E123"). */
    ranges: ReadonlyMap<string, readonly [number, number]>;
    /** The mesh's index list. */
    index: ArrayLike<number>;
    /** The node a vertex of the mesh is. Absent: the vertex itself. */
    nodeOf?(vertex: number): number;
    /** Write where a vertex is now, in world space and at the deformation shown, to
     *  `out[at .. at + 2]`. False when it is not known. */
    position(vertex: number, out: Float32Array, at: number): boolean;
    isHidden?(key: string): boolean;
}

/** Where `count` world points (xyz each, in `world`) are in the view: per point, x and y in CSS
 *  pixels from its top-left corner and the depth (0 at the near plane, 1 at the far one) into
 *  `out`; a point behind the camera gets NaN. Returns the view's size in CSS pixels. */
export type ScreenProjector = (world: Float32Array, count: number, out: Float32Array) => {width: number; height: number};

export interface LabelPlacementOptions {
    nodes: boolean;
    elements: boolean;
    /**
     * Label each element with its value (`valueText`) rather than its number: the shown result or a
     * model property. Picked and thinned like the numbers: same selection, same screen grid.
     */
    values?: boolean;
    /** An element's value as text from its number; undefined = no label for that element. */
    valueText?: (elementNumber: number) => string | undefined;
    /** The most labels returned. */
    cap: number;
    /** The most elements examined in one pass. */
    scanCap: number;
    /** Element keys to label; none or empty = sample the whole mesh. */
    selected?: ReadonlySet<string> | null;
    /** A node's text from its index; default `index + 1`. */
    nodeText?: (node: number) => string;
    /** Decluttering grid, px. */
    cellW?: number;
    cellH?: number;
}

/** An element's number from its key ("E123" -> "123"). */
export function elementText(key: string): string {
    return key.replace(/^\D+/, "") || key;
}

/** The elements examined: the selected ones, else an even sample of the mesh's ranges. */
function pickElements(source: LabelSource, selected: ReadonlySet<string> | null | undefined, scanCap: number): string[] {
    const ranges = source.ranges;
    const out: string[] = [];
    if (selected && selected.size) {
        for (const k of selected) {
            if (ranges.has(k) && !source.isHidden?.(k)) out.push(k);
            if (out.length >= scanCap) break;
        }
        return out;
    }
    const stride = Math.max(1, Math.ceil(ranges.size / scanCap));
    let i = 0;
    for (const k of ranges.keys()) {
        if (i++ % stride === 0 && !source.isHidden?.(k)) out.push(k);
    }
    return out;
}

interface Candidates {
    keys: string[];
    kinds: LabelKind[];
    texts: string[];
    /** xyz per candidate. */
    world: Float32Array;
}

/** Candidates (world positions) for the elements picked. */
function candidates(source: LabelSource, keys: readonly string[], opts: LabelPlacementOptions): Candidates {
    const {index, ranges} = source;
    const nodeText = opts.nodeText ?? ((i: number) => String(i + 1));
    const seenNodes = new Set<number>();
    const out: Candidates = {keys: [], kinds: [], texts: [], world: new Float32Array(Math.max(1, keys.length) * 3 * (opts.nodes ? 5 : 1))};
    const p = new Float32Array(3);
    const push = (key: string, kind: LabelKind, text: string, x: number, y: number, z: number) => {
        const at = out.keys.length * 3;
        if (at + 3 > out.world.length) {
            const grown = new Float32Array(out.world.length * 2);
            grown.set(out.world);
            out.world = grown;
        }
        out.world[at] = x;
        out.world[at + 1] = y;
        out.world[at + 2] = z;
        out.keys.push(key);
        out.kinds.push(kind);
        out.texts.push(text);
    };
    const verts: number[] = [];
    for (const key of keys) {
        const r = ranges.get(key);
        if (!r || r[1] <= 0) continue;
        const [start, count] = r;
        // An element's vertices: the range's indices, de-duplicated, capped so one huge face costs little.
        verts.length = 0;
        const step = Math.max(1, Math.floor(count / 48));
        for (let j = start; j < start + count && j < index.length; j += step) {
            const v = index[j];
            if (!verts.includes(v)) verts.push(v);
        }
        const valueText = opts.values ? opts.valueText?.(Number(elementText(key))) : undefined;
        if (opts.elements || valueText !== undefined) {
            let sx = 0, sy = 0, sz = 0, n = 0;
            for (const v of verts) {
                if (!source.position(v, p, 0)) continue;
                sx += p[0];
                sy += p[1];
                sz += p[2];
                n++;
            }
            if (n) {
                if (valueText !== undefined) push(`v:${key}`, "value", valueText, sx / n, sy / n, sz / n);
                else push(`e:${key}`, "element", elementText(key), sx / n, sy / n, sz / n);
            }
        }
        if (opts.nodes) {
            for (const v of verts) {
                const node = source.nodeOf ? source.nodeOf(v) : v;
                if (seenNodes.has(node)) continue;
                seenNodes.add(node);
                if (source.position(v, p, 0)) push(`n:${node}`, "node", nodeText(node), p[0], p[1], p[2]);
            }
        }
    }
    return out;
}

// Values, then element numbers, then nodes: where several are on, the earlier wins the cell.
const RANK: Record<LabelKind, number> = {value: 0, element: 1, node: 2};

/** The labels of `source` as `project` sees it. */
export function placeLabels(source: LabelSource | null, project: ScreenProjector | null, opts: LabelPlacementOptions): ScreenLabel[] {
    if (!source || !project || (!opts.nodes && !opts.elements && !opts.values)) return [];
    const keys = pickElements(source, opts.selected, opts.scanCap);
    if (!keys.length) return [];
    const cands = candidates(source, keys, opts);
    const n = cands.keys.length;
    if (!n) return [];
    const screen = new Float32Array(n * 3);
    const {width, height} = project(cands.world, n, screen);
    if (width <= 0 || height <= 0) return [];
    const cellW = opts.cellW ?? 44;
    const cellH = opts.cellH ?? 14;
    const taken = new Set<string>();
    const out: ScreenLabel[] = [];
    // By rank, each kind in the order it was found.
    for (let rank = 0; rank < 3 && out.length < opts.cap; rank++) {
        for (let i = 0; i < n; i++) {
            if (RANK[cands.kinds[i]] !== rank) continue;
            const x = screen[i * 3], y = screen[i * 3 + 1], depth = screen[i * 3 + 2];
            if (!(depth >= 0 && depth <= 1) || !(x >= 0 && x <= width) || !(y >= 0 && y <= height)) continue;
            const cell = `${Math.floor(x / cellW)}:${Math.floor(y / cellH)}`;
            if (taken.has(cell)) continue;
            taken.add(cell);
            out.push({key: cands.keys[i], kind: cands.kinds[i], text: cands.texts[i], x, y});
            if (out.length >= opts.cap) break;
        }
    }
    return out;
}

/** A projector from a camera's two matrices (column-major 4x4: world to view, view to clip) and
 *  the view's size in CSS pixels. `clipZ` says where the near plane is in clip space: -1 as
 *  WebGL has it, 0 as WebGPU does. */
export function matrixProjector(
    view: ArrayLike<number>, proj: ArrayLike<number>, width: number, height: number, clipZ: -1 | 0,
): ScreenProjector {
    return (world, count, out) => {
        for (let i = 0, at = 0; i < count; i++, at += 3) {
            const x = world[at], y = world[at + 1], z = world[at + 2];
            const ex = view[0] * x + view[4] * y + view[8] * z + view[12];
            const ey = view[1] * x + view[5] * y + view[9] * z + view[13];
            const ez = view[2] * x + view[6] * y + view[10] * z + view[14];
            const ew = view[3] * x + view[7] * y + view[11] * z + view[15];
            const cx = proj[0] * ex + proj[4] * ey + proj[8] * ez + proj[12] * ew;
            const cy = proj[1] * ex + proj[5] * ey + proj[9] * ez + proj[13] * ew;
            const cz = proj[2] * ex + proj[6] * ey + proj[10] * ez + proj[14] * ew;
            const cw = proj[3] * ex + proj[7] * ey + proj[11] * ez + proj[15] * ew;
            if (!(cw > 1e-9)) {
                out[at] = out[at + 1] = out[at + 2] = NaN;
                continue;
            }
            out[at] = (cx / cw * 0.5 + 0.5) * width;
            out[at + 1] = (0.5 - cy / cw * 0.5) * height;
            out[at + 2] = clipZ === 0 ? cz / cw : cz / cw * 0.5 + 0.5;
        }
        return {width, height};
    };
}
