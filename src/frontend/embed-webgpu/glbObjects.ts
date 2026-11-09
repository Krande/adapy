// Which model object a pick hit, for the WebGPU embed's object info. adacpp's GLB reader (glb_scene.cpp)
// merges every primitive of one topology + material into one geometry and keeps each adapy draw range
// ("draw_ranges_<node>" in the scene extras) as a segment; a pick reports the geometry's instance and the
// segment. The reader does not hand back which object a segment is, so this replays its batching on the
// GLB's JSON alone (accessor counts, no buffers) to name each segment. `matchGeometries` checks the replay
// against what the reader actually built (topology, vertex and segment counts) and drops any geometry that
// disagrees, so a change in the reader shows as "no object info", never as a wrong name.
import {Topology} from "../src/viewport/adacpp/types";

/** What a segment draws: an adapy object (`id` from the draw ranges) or a glTF node (no ranges). */
export interface SegmentObject {
    id?: string;
    /** The glTF node name, when the segment is a whole primitive. */
    node?: string;
}

export interface BatchRecord {
    topology: number;
    vertexCount: number;
    /** One entry per segment, in the geometry's segment order; null = a gap between draw ranges. */
    segments: (SegmentObject | null)[];
}

// glb_scene.cpp Batcher's flush limits.
const MAX_VERTICES = 2_000_000;
const MAX_INDICES = 12_000_000;

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

/** The JSON chunk of a GLB, or null when the bytes are not one. */
export function glbJson(bytes: Uint8Array): Json | null {
    if (bytes.byteLength < 20) return null;
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (dv.getUint32(0, true) !== 0x46546c67) return null; // "glTF"
    const len = dv.getUint32(12, true);
    if (dv.getUint32(16, true) !== 0x4e4f534a || 20 + len > bytes.byteLength) return null; // "JSON"
    try {
        return JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + len)));
    } catch {
        return null;
    }
}

/** glb_scene.cpp segments_from_extras, keeping each range's object id. */
export function segmentsFromRanges(
    ranges: Record<string, unknown> | null | undefined,
    indexCount: number,
): [number, number, SegmentObject | null][] {
    const rs: [number, number, string][] = [];
    if (ranges && typeof ranges === "object") {
        for (const [id, v] of Object.entries(ranges)) {
            if (!Array.isArray(v) || v.length < 2) continue;
            const num = (x: unknown) => (typeof x === "number" ? Math.max(0, Math.trunc(x)) : 0);
            rs.push([num(v[0]), num(v[1]), id]);
        }
    }
    rs.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const out: [number, number, SegmentObject | null][] = [];
    let cursor = 0;
    for (const [start, count, id] of rs) {
        if (start >= indexCount || count === 0) continue;
        if (start > cursor) out.push([cursor, start - cursor, null]);
        const c = Math.min(count, indexCount - start);
        out.push([start, c, {id}]);
        cursor = Math.max(cursor, start + c);
    }
    if (out.length && cursor < indexCount) out.push([cursor, indexCount - cursor, null]);
    return out;
}

interface Batch {
    key: [number, number, number];
    vertexCount: number;
    indexCount: number;
    segments: (SegmentObject | null)[];
}

/** The geometries adacpp's GLB reader builds from `json`, in the order it reports them (its
 *  `geometries`, feature-edge overlays left out). */
export function replayBatches(json: Json): BatchRecord[] {
    const out: BatchRecord[] = [];
    const batches = new Map<string, Batch>();
    const accessors: Json[] = json?.accessors ?? [];
    const flush = (b: Batch) => {
        if (b.vertexCount === 0) return;
        out.push({topology: b.key[0], vertexCount: b.vertexCount, segments: b.segments});
        b.vertexCount = 0;
        b.indexCount = 0;
        b.segments = [];
    };
    const add = (prim: Json, node: Json, ranges: Record<string, unknown> | null) => {
        const mode = prim.mode ?? 4;
        const topo = mode === 4 ? Topology.Triangles : mode === 1 ? Topology.Lines : mode === 0 ? Topology.Points : -1;
        if (topo < 0) return;
        const pos = prim.attributes?.POSITION;
        if (pos === undefined) return;
        const nv: number = accessors[pos]?.count ?? 0;
        const indexCount: number = prim.indices !== undefined ? (accessors[prim.indices]?.count ?? 0) : nv;
        const colored = prim.attributes?.COLOR_0 !== undefined && topo !== Topology.Triangles;
        const key: [number, number, number] = [topo, prim.material ?? -1, colored ? 1 : 0];
        const k = key.join("|");
        let b = batches.get(k);
        if (!b) batches.set(k, (b = {key, vertexCount: 0, indexCount: 0, segments: []}));
        if (b.vertexCount + nv > MAX_VERTICES || b.indexCount + indexCount > MAX_INDICES) flush(b);
        let segs = topo === Topology.Triangles ? segmentsFromRanges(ranges, indexCount) : [];
        if (!segs.length) segs = [[0, indexCount, node?.name ? {node: String(node.name)} : null]];
        for (const s of segs) b.segments.push(s[2]);
        b.vertexCount += nv;
        b.indexCount += indexCount;
    };
    const nodes: Json[] = json?.nodes ?? [];
    const meshes: Json[] = json?.meshes ?? [];
    const walk = (i: number, extras: Json, depth: number) => {
        const node = nodes[i];
        if (!node || depth > 256) return;
        if (node.mesh !== undefined) {
            const prims: Json[] = meshes[node.mesh]?.primitives ?? [];
            const key = `draw_ranges_${node.name ?? ""}`;
            const ranges = prims.length === 1 && extras && typeof extras === "object" && key in extras ? extras[key] : null;
            for (const p of prims) add(p, node, ranges);
        }
        for (const c of node.children ?? []) walk(c, extras, depth + 1);
    };
    const scenes: Json[] = json?.scenes ?? [];
    if (scenes.length) {
        const scene = scenes[json.scene ?? 0] ?? scenes[0];
        for (const n of scene?.nodes ?? []) walk(n, scene?.extras, 0);
    } else {
        for (let n = 0; n < nodes.length; n++) walk(n, null, 0);
    }
    // std::map order: topology, then material, then coloured.
    const ordered = [...batches.values()].sort(
        (a, b) => a.key[0] - b.key[0] || a.key[1] - b.key[1] || a.key[2] - b.key[2],
    );
    for (const b of ordered) flush(b);
    return out;
}

/** Per loaded instance, its segments' objects -- only where the replay agrees with what was loaded. */
export function matchGeometries(
    records: BatchRecord[],
    loaded: {geometry: number; instance: number; topology: number; vertexCount: number; featureEdges: boolean}[],
    segmentCount: (geometry: number) => number,
): Map<number, (SegmentObject | null)[]> {
    const map = new Map<number, (SegmentObject | null)[]>();
    const real = loaded.filter((g) => !g.featureEdges);
    if (real.length !== records.length) return map;
    real.forEach((g, i) => {
        const r = records[i];
        if (r.topology === g.topology && r.vertexCount === g.vertexCount && segmentCount(g.geometry) === r.segments.length) {
            map.set(g.instance, r.segments);
        }
    });
    return map;
}

/** An object's name and its parents' (outermost first), from the scene's "id_hierarchy"
 *  ({id: [name, parent id]}). */
export function objectPath(json: Json, id: string): string[] {
    const hier = json?.scenes?.[json?.scene ?? 0]?.extras?.id_hierarchy as Record<string, [string, string]> | undefined;
    if (!hier) return [];
    const path: string[] = [];
    const seen = new Set<string>();
    let cur: string | undefined = id;
    while (cur !== undefined && cur !== "*" && hier[cur] && !seen.has(cur)) {
        seen.add(cur);
        path.unshift(String(hier[cur][0]));
        cur = hier[cur][1] === undefined ? undefined : String(hier[cur][1]);
    }
    return path;
}
