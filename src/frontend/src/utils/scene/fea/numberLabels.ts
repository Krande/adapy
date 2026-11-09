// Number labels over a loaded FEA result: node ids and element ids, placed in
// screen space.
//
// Labelling every node of a million-node model is neither affordable (an HTML
// node each) nor readable, so this picks what is worth a label:
//
//   * With a selection on the result mesh, only the selected elements (and their
//     nodes) are candidates: "which node is that" is asked about a few elements.
//   * With none, the elements are sampled to `scanCap`, so the cost of a pass is
//     bounded whatever the model's size.
//   * Candidates behind the camera or off screen are dropped, and the rest are
//     thinned on a screen grid (`cellW` x `cellH` px, one label per cell), which
//     both declutters and caps the count: zoom in and the cells hold fewer
//     candidates, so more of them get a label.
//
// Positions follow the deformed shape (the morph state of the mesh), through the
// same lookup "go to node" uses. Hidden elements get no label. Occlusion is not
// tested: a label is drawn for a node behind the surface in front of it.

import * as THREE from "three";

import {vertexWorldPosition} from "./goToNode";

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

export interface LabelOptions {
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
    /** Draw-range keys to label; none or empty = sample the whole mesh. */
    selected?: ReadonlySet<string> | null;
    /** Size of the view in CSS pixels. */
    width: number;
    height: number;
    /** A node's text from its vertex index; default `index + 1`. */
    nodeText?: (vertexIndex: number) => string;
    /** Decluttering grid, px. */
    cellW?: number;
    cellH?: number;
}

/** An element's number from its draw-range key ("E123" -> "123"). */
export function elementText(key: string): string {
    return key.replace(/^\D+/, "") || key;
}

interface MeshLike extends THREE.Mesh {
    drawRanges?: Map<string, [number, number]>;
    isRangeHidden?: (id: string) => boolean;
}

interface Candidate {
    key: string;
    kind: LabelKind;
    text: string;
    pos: THREE.Vector3;
}

/** The elements examined: the selected ones, else an even sample of the mesh's ranges. */
function pickElements(mesh: MeshLike, selected: ReadonlySet<string> | null | undefined, scanCap: number): string[] {
    const ranges = mesh.drawRanges;
    if (!ranges) return [];
    const out: string[] = [];
    if (selected && selected.size) {
        for (const k of selected) {
            if (ranges.has(k) && !mesh.isRangeHidden?.(k)) out.push(k);
            if (out.length >= scanCap) break;
        }
        return out;
    }
    const stride = Math.max(1, Math.ceil(ranges.size / scanCap));
    let i = 0;
    for (const k of ranges.keys()) {
        if (i++ % stride === 0 && !mesh.isRangeHidden?.(k)) out.push(k);
    }
    return out;
}

/** Candidates (world positions) for the elements picked. */
function candidates(mesh: MeshLike, keys: readonly string[], opts: LabelOptions): Candidate[] {
    const index = mesh.geometry.getIndex();
    const ranges = mesh.drawRanges;
    if (!index || !ranges) return [];
    const nodeText = opts.nodeText ?? ((i: number) => String(i + 1));
    const seenNodes = new Set<number>();
    const out: Candidate[] = [];
    const sum = new THREE.Vector3();
    for (const key of keys) {
        const r = ranges.get(key);
        if (!r || r[1] <= 0) continue;
        const [start, count] = r;
        // An element's vertices: the range's indices, de-duplicated, capped so one huge face costs little.
        const verts: number[] = [];
        const step = Math.max(1, Math.floor(count / 48));
        for (let j = start; j < start + count && j < index.count; j += step) {
            const v = index.getX(j);
            if (!verts.includes(v)) verts.push(v);
        }
        const valueText = opts.values ? opts.valueText?.(Number(elementText(key))) : undefined;
        if (opts.elements || valueText !== undefined) {
            sum.set(0, 0, 0);
            let n = 0;
            for (const v of verts) {
                const p = vertexWorldPosition(mesh, v);
                if (!p) continue;
                sum.add(p);
                n++;
            }
            if (n) {
                const pos = sum.clone().multiplyScalar(1 / n);
                if (valueText !== undefined) out.push({key: `v:${key}`, kind: "value", text: valueText, pos});
                else out.push({key: `e:${key}`, kind: "element", text: elementText(key), pos});
            }
        }
        if (opts.nodes) {
            for (const v of verts) {
                if (seenNodes.has(v)) continue;
                seenNodes.add(v);
                const p = vertexWorldPosition(mesh, v);
                if (p) out.push({key: `n:${v}`, kind: "node", text: nodeText(v), pos: p});
            }
        }
    }
    return out;
}

/** The labels for `mesh` seen from `camera`. */
export function collectLabels(mesh: THREE.Mesh | null, camera: THREE.Camera | null, opts: LabelOptions): ScreenLabel[] {
    if (!mesh || !camera || (!opts.nodes && !opts.elements && !opts.values) || opts.width <= 0 || opts.height <= 0) return [];
    const m = mesh as MeshLike;
    const keys = pickElements(m, opts.selected, opts.scanCap);
    if (!keys.length) return [];
    const cellW = opts.cellW ?? 44;
    const cellH = opts.cellH ?? 14;
    const taken = new Set<string>();
    const out: ScreenLabel[] = [];
    const v = new THREE.Vector3();
    // Values, then element numbers, then nodes: where several are on, the earlier wins the cell.
    const rank = (k: LabelKind) => (k === "value" ? 0 : k === "element" ? 1 : 2);
    const cands = candidates(m, keys, opts).sort((a, b) => rank(a.kind) - rank(b.kind));
    for (const c of cands) {
        v.copy(c.pos).project(camera);
        if (!(v.z >= -1 && v.z <= 1) || Math.abs(v.x) > 1 || Math.abs(v.y) > 1) continue;
        const x = (v.x * 0.5 + 0.5) * opts.width;
        const y = (-v.y * 0.5 + 0.5) * opts.height;
        const cell = `${Math.floor(x / cellW)}:${Math.floor(y / cellH)}`;
        if (taken.has(cell)) continue;
        taken.add(cell);
        out.push({key: c.key, kind: c.kind, text: c.text, x, y});
        if (out.length >= opts.cap) break;
    }
    return out;
}
