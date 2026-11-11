// FEA streaming: the coloured lines of line elements, on a result drawn through result layers.
//
// Owns: which of the lines a paint worked out are drawn (not those of the beams that only carry a
// mass to its centre of gravity), and the lines as objects of the viewport, each the object its
// element is, so hiding, showing, isolating and selecting an element reach a beam that only a line
// draws.
//
// No three.js in this module or in anything it imports.

import type {ResultMeshObjects} from "@/viewport/api/resultLayer";
import type {PaintedPoints} from "../elemFieldPaint";

/** The element labels of ids like ``E12``. */
export function elementLabels(ids: readonly string[]): ReadonlySet<number> {
    const labels = new Set<number>();
    for (const id of ids) {
        const label = Number(id.slice(1));
        if (id.startsWith("E") && Number.isInteger(label)) labels.add(label);
    }
    return labels;
}

/** `lines` (two points per line element, `labels` naming the element of each point) without the
 *  lines of the elements in `drop`, and with the labels beside the points. The arrays of `lines`
 *  and `labels` are compacted in place: they are the paint's own, made for this one frame. */
export function linesWithout(lines: PaintedPoints, labels: number[], drop: ReadonlySet<number> | null): PaintedPoints {
    let kept = labels.length;
    if (drop && drop.size > 0) {
        kept = 0;
        const {positions, colors, displacement} = lines;
        for (let p = 0; p < labels.length; p++) {
            if (drop.has(labels[p])) continue;
            if (kept !== p) {
                const from = p * 3, to = kept * 3;
                for (let k = 0; k < 3; k++) {
                    positions[to + k] = positions[from + k];
                    colors[to + k] = colors[from + k];
                    displacement[to + k] = displacement[from + k];
                }
                labels[kept] = labels[p];
            }
            kept++;
        }
    }
    if (kept === labels.length) return {positions: lines.positions, colors: lines.colors, displacement: lines.displacement, labels};
    labels.length = kept;
    return {
        positions: lines.positions.subarray(0, kept * 3),
        colors: lines.colors.subarray(0, kept * 3),
        displacement: lines.displacement.subarray(0, kept * 3),
        labels,
    };
}

/** Lines drawn in order, two points each, as objects of `model`: each line is the object its
 *  element is (`indexOf` gives an element's place among the model's ids, or nothing when it is not
 *  one of them). */
export function lineObjects(
    model: ResultMeshObjects,
    labels: readonly number[],
    indexOf: (label: number) => number | undefined,
): ResultMeshObjects {
    const count = labels.length >> 1;
    const segments = new Uint32Array(count * 2);
    const objectOf = new Int32Array(count);
    for (let line = 0; line < count; line++) {
        segments[line * 2] = line * 2;
        segments[line * 2 + 1] = 2;
        objectOf[line] = indexOf(labels[line * 2]) ?? -1;
    }
    return {model: model.model, sourceName: model.sourceName, ids: model.ids, segments, objectOf};
}
