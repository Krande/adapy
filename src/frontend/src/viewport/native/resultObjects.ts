// The objects of a result mesh (viewport/api/resultLayer.ts: the elements of an FEA mesh) as the
// three.js-free scene keeps a model's objects: the renderer's segments, each object's box, and
// which segments draw which object. Plain typed arrays; no three.js and no renderer in this module.
import type {ResultMeshData} from "@/viewport/api/resultLayer";
import type {LoadedGlbObjects} from "@/viewport/adacpp/types";

/** A result mesh's object ranges as the renderer's segments: the ranges given, with whatever lies
 *  between them as segments of no object, so the index list is covered in order. */
export function resultSegments(data: ResultMeshData): {segments: Uint32Array; objectOf: Int32Array} {
    const {segments, objectOf} = data.objects!;
    const indexCount = data.indices ? data.indices.length : data.positions.length / 3;
    const pairs: number[] = [];
    const objects: number[] = [];
    let cursor = 0;
    for (let s = 0; s < objectOf.length; s++) {
        const start = segments[s * 2], count = segments[s * 2 + 1];
        if (count === 0) continue;
        if (start > cursor) {
            pairs.push(cursor, start - cursor);
            objects.push(-1);
        }
        pairs.push(start, count);
        objects.push(objectOf[s]);
        cursor = start + count;
    }
    if (cursor < indexCount) {
        pairs.push(cursor, indexCount - cursor);
        objects.push(-1);
    }
    return {segments: Uint32Array.from(pairs), objectOf: Int32Array.from(objects)};
}

/** Widen each object's box (`bounds`, 6 per object) by what `data` draws of it. */
export function growObjectBounds(bounds: Float32Array, data: ResultMeshData): void {
    const {segments} = data.objects!;
    const {positions, indices} = data;
    const m = data.matrix ?? [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    const given = data.objects!.objectOf;
    for (let s = 0; s < given.length; s++) {
        const object = given[s];
        if (object < 0) continue;
        const start = segments[s * 2], end = start + segments[s * 2 + 1], at = object * 6;
        for (let i = start; i < end; i++) {
            const v = (indices ? indices[i] : i) * 3;
            const x = positions[v], y = positions[v + 1], z = positions[v + 2];
            for (let k = 0; k < 3; k++) {
                const w = m[k] * x + m[4 + k] * y + m[8 + k] * z + m[12 + k];
                if (w < bounds[at + k]) bounds[at + k] = w;
                if (w > bounds[at + k + 3]) bounds[at + k + 3] = w;
            }
        }
    }
}

/** Which (instance, segment) pairs draw each object of a result model, from the layers it has now
 *  (each by its instance, with the object of each of its segments): written to the model's table. */
export function indexResultSegments(
    table: Pick<LoadedGlbObjects, "count" | "segmentOffsets" | "segments">,
    layers: ReadonlyMap<number, {objectOf: Int32Array}>,
): void {
    const offsets = new Uint32Array(table.count + 1);
    for (const {objectOf} of layers.values()) {
        for (let s = 0; s < objectOf.length; s++) if (objectOf[s] >= 0) offsets[objectOf[s] + 1]++;
    }
    for (let i = 0; i < table.count; i++) offsets[i + 1] += offsets[i];
    const pairs = new Uint32Array(offsets[table.count] * 2);
    const fill = offsets.slice(0, table.count);
    for (const [instance, {objectOf}] of layers) {
        for (let s = 0; s < objectOf.length; s++) {
            const object = objectOf[s];
            if (object < 0) continue;
            const at = fill[object]++ * 2;
            pairs[at] = instance;
            pairs[at + 1] = s;
        }
    }
    table.segmentOffsets = offsets;
    table.segments = pairs;
}
