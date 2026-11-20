// One object's triangles out of a GLB, read without a scene library: what the three.js-free viewport
// finds an object's feature edges from (the Measure tab snaps to them), since its renderer keeps the
// model's geometry on the GPU and hands none of it back.
//
// The objects are the ones that viewport's reader (adacpp) makes of the same file:
//   - a GLB with adapy's scene extras: "draw_ranges_<node name>": {object id: [start, count]} names
//     each object's share of a merged node's index buffer (one primitive per such node);
//   - any other GLB: every node is an object, its id the node's index in decimal, drawn by its mesh.
// Positions come out in the world: each node's transform and then `offset` applied in double
// precision, as that reader places them, before they become 32-bit. Triangles only; a compressed
// primitive (Draco, meshopt) is skipped. No three.js in this module.

export interface ObjectTriangles {
    /** xyz per vertex, in the world (only the vertices the triangles use). */
    positions: Float32Array;
    /** Three per triangle, into `positions`. */
    indices: Uint32Array;
}

export interface GlbObjectReader {
    /** The triangles that draw object `id`, placed by `offset`; null when it draws none. */
    triangles(id: string, offset?: readonly [number, number, number]): ObjectTriangles | null;
    /** The vertices object `id`'s triangles use, xyz each in the world, placed by `offset`: each once,
     *  in the order its file lists them (a mesh's own vertex order, what three.js' loader keeps); null
     *  when it draws none. */
    vertices(id: string, offset?: readonly [number, number, number]): Float32Array | null;
}

const GLB_MAGIC = 0x46546c67; // "glTF"
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;
const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const DRAW_RANGES = "draw_ranges_";

interface Accessor {
    bufferView?: number;
    byteOffset?: number;
    componentType: number;
    count: number;
    type: string;
    sparse?: unknown;
}
interface BufferView {
    buffer: number;
    byteOffset?: number;
    byteLength: number;
    byteStride?: number;
}
interface Primitive {
    attributes: Record<string, number>;
    indices?: number;
    mode?: number;
    extensions?: Record<string, unknown>;
}
interface GltfNode {
    name?: string;
    mesh?: number;
    children?: number[];
    matrix?: number[];
    translation?: number[];
    rotation?: number[];
    scale?: number[];
}
interface Gltf {
    accessors?: Accessor[];
    bufferViews?: BufferView[];
    meshes?: {primitives?: Primitive[]}[];
    nodes?: GltfNode[];
    scenes?: {nodes?: number[]; extras?: Record<string, unknown>}[];
    scene?: number;
}

/** a * b, both column-major 4x4. */
function multiply(a: ArrayLike<number>, b: ArrayLike<number>): number[] {
    const out = new Array<number>(16);
    for (let col = 0; col < 4; col++) {
        for (let row = 0; row < 4; row++) {
            let sum = 0;
            for (let k = 0; k < 4; k++) sum += a[k * 4 + row] * b[col * 4 + k];
            out[col * 4 + row] = sum;
        }
    }
    return out;
}

/** A node's own transform: its matrix, or its translation * rotation * scale. */
function localMatrix(node: GltfNode): number[] {
    if (node.matrix?.length === 16) return node.matrix;
    const [tx, ty, tz] = node.translation ?? [0, 0, 0];
    const [x, y, z, w] = node.rotation ?? [0, 0, 0, 1];
    const [sx, sy, sz] = node.scale ?? [1, 1, 1];
    return [
        (1 - 2 * (y * y + z * z)) * sx, 2 * (x * y + z * w) * sx, 2 * (x * z - y * w) * sx, 0,
        2 * (x * y - z * w) * sy, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z + x * w) * sy, 0,
        2 * (x * z + y * w) * sz, 2 * (y * z - x * w) * sz, (1 - 2 * (x * x + y * y)) * sz, 0,
        tx, ty, tz, 1,
    ];
}

/** Where an object's triangles are: a primitive, placed by a node, and a range of its index list
 *  (the whole list when `count` is -1). */
interface Part {
    primitive: Primitive;
    matrix: number[];
    start: number;
    count: number;
}

const num = (v: unknown): number => (typeof v === "number" ? v : Number(v));

/** Read a GLB's JSON and binary chunks once; each object's triangles are read when asked for. Throws
 *  on a file that is not a binary glTF. */
export function glbObjectReader(bytes: Uint8Array): GlbObjectReader {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (bytes.byteLength < 20 || view.getUint32(0, true) !== GLB_MAGIC) throw new Error("not a binary glTF (GLB) file");
    let gltf: Gltf | null = null;
    let bin: Uint8Array | null = null;
    for (let offset = 12; offset + 8 <= bytes.byteLength; ) {
        const length = view.getUint32(offset, true);
        const type = view.getUint32(offset + 4, true);
        const chunk = bytes.subarray(offset + 8, offset + 8 + length);
        if (type === CHUNK_JSON) gltf = JSON.parse(new TextDecoder().decode(chunk)) as Gltf;
        else if (type === CHUNK_BIN && !bin) bin = chunk;
        offset += 8 + length;
    }
    if (!gltf) throw new Error("the GLB has no JSON chunk");
    const doc = gltf;
    const sceneIndex = doc.scene ?? 0;
    const extras = doc.scenes?.[sceneIndex]?.extras ?? {};
    const adapy = Object.keys(extras).some((k) => k.startsWith(DRAW_RANGES));

    // Where each object is drawn, found in one walk of the scene on first use.
    let parts: Map<string, Part[]> | null = null;
    const index = (): Map<string, Part[]> => {
        if (parts) return parts;
        const found = new Map<string, Part[]>();
        const add = (id: string, part: Part) => {
            const list = found.get(id);
            if (list) list.push(part);
            else found.set(id, [part]);
        };
        const nodes = doc.nodes ?? [];
        const roots = doc.scenes?.[sceneIndex]?.nodes ?? nodes.map((_, i) => i);
        const stack: {node: number; parent: number[]}[] = roots.map((node) => ({node, parent: IDENTITY})).reverse();
        const seen = new Set<number>();
        while (stack.length) {
            const {node, parent} = stack.pop()!;
            const n = nodes[node];
            if (!n || seen.has(node)) continue;
            seen.add(node);
            const world = multiply(parent, localMatrix(n));
            const primitives = n.mesh !== undefined ? (doc.meshes?.[n.mesh]?.primitives ?? []) : [];
            if (adapy) {
                const ranges = primitives.length === 1 && n.name !== undefined ? extras[DRAW_RANGES + n.name] : undefined;
                if (ranges && typeof ranges === "object") {
                    for (const [id, range] of Object.entries(ranges as Record<string, unknown>)) {
                        if (!Array.isArray(range) || range.length < 2) continue;
                        add(id, {primitive: primitives[0], matrix: world, start: num(range[0]), count: num(range[1])});
                    }
                }
            } else {
                for (const primitive of primitives) add(String(node), {primitive, matrix: world, start: 0, count: -1});
            }
            for (const child of [...(n.children ?? [])].reverse()) stack.push({node: child, parent: world});
        }
        return (parts = found);
    };

    /** An accessor's element `i`, component `c`, as a number. */
    const reader = (accessorIndex: number): ((i: number, c: number) => number) | null => {
        const accessor = doc.accessors?.[accessorIndex];
        if (!accessor || accessor.bufferView === undefined || accessor.sparse || !bin) return null;
        const bufferView = doc.bufferViews?.[accessor.bufferView];
        if (!bufferView || bufferView.buffer !== 0) return null;
        const components = accessor.type === "VEC3" ? 3 : accessor.type === "SCALAR" ? 1 : 0;
        if (!components) return null;
        const size = accessor.componentType === 5126 || accessor.componentType === 5125 ? 4 : accessor.componentType === 5123 ? 2 : accessor.componentType === 5121 ? 1 : 0;
        if (!size) return null;
        const stride = bufferView.byteStride || size * components;
        const start = bin.byteOffset + (bufferView.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
        const data = new DataView(bin.buffer, start, Math.max(0, (accessor.count - 1) * stride + size * components));
        switch (accessor.componentType) {
            case 5126: return (i, c) => data.getFloat32(i * stride + c * 4, true);
            case 5125: return (i, c) => data.getUint32(i * stride + c * 4, true);
            case 5123: return (i, c) => data.getUint16(i * stride + c * 2, true);
            default: return (i, c) => data.getUint8(i * stride + c);
        }
    };

    /** Each readable part of object `id`: its vertex reader, where the node places it, and the
     *  corners of each of its triangles (indices into the primitive's vertices), in order. */
    const partsOf = (id: string): {pos: (i: number, c: number) => number; matrix: number[]; corners: number[]}[] => {
        const out: {pos: (i: number, c: number) => number; matrix: number[]; corners: number[]}[] = [];
        for (const {primitive, matrix, start, count} of index().get(id) ?? []) {
            if ((primitive.mode ?? 4) !== 4) continue;
            if (Object.keys(primitive.extensions ?? {}).some((e) => /draco|meshopt/i.test(e))) continue;
            const posAccessor = primitive.attributes?.POSITION;
            const pos = posAccessor !== undefined ? reader(posAccessor) : null;
            const posCount = posAccessor !== undefined ? (doc.accessors?.[posAccessor]?.count ?? 0) : 0;
            if (!pos) continue;
            const idx = primitive.indices !== undefined ? reader(primitive.indices) : null;
            const idxCount = primitive.indices !== undefined ? (doc.accessors?.[primitive.indices]?.count ?? 0) : posCount;
            if (primitive.indices !== undefined && !idx) continue;
            const from = Math.max(0, start);
            const to = Math.min(idxCount, count < 0 ? idxCount : from + count);
            const corners: number[] = [];
            for (let k = from; k + 2 < to; k += 3) {
                const a = idx ? idx(k, 0) : k, b = idx ? idx(k + 1, 0) : k + 1, c = idx ? idx(k + 2, 0) : k + 2;
                if (a >= posCount || b >= posCount || c >= posCount) continue;
                corners.push(a, b, c);
            }
            out.push({pos, matrix, corners});
        }
        return out;
    };

    /** Vertex `v` of a part, placed by its node and then `offset`, onto the end of `out`. */
    const place = (out: number[], pos: (i: number, c: number) => number, m: number[], v: number, offset: readonly [number, number, number]): void => {
        const x = pos(v, 0), y = pos(v, 1), z = pos(v, 2);
        out.push(
            m[0] * x + m[4] * y + m[8] * z + m[12] + offset[0],
            m[1] * x + m[5] * y + m[9] * z + m[13] + offset[1],
            m[2] * x + m[6] * y + m[10] * z + m[14] + offset[2],
        );
    };

    return {
        triangles(id, offset = [0, 0, 0]) {
            const positions: number[] = [];
            const indices: number[] = [];
            for (const {pos, matrix, corners} of partsOf(id)) {
                // Only the vertices the range uses, each placed once.
                const remap = new Map<number, number>();
                for (const v of corners) {
                    let at = remap.get(v);
                    if (at === undefined) {
                        at = positions.length / 3;
                        remap.set(v, at);
                        place(positions, pos, matrix, v, offset);
                    }
                    indices.push(at);
                }
            }
            if (!indices.length) return null;
            return {positions: Float32Array.from(positions), indices: Uint32Array.from(indices)};
        },
        vertices(id, offset = [0, 0, 0]) {
            const positions: number[] = [];
            for (const {pos, matrix, corners} of partsOf(id)) {
                const used = Array.from(new Set(corners)).sort((a, b) => a - b);
                for (const v of used) place(positions, pos, matrix, v, offset);
            }
            return positions.length ? Float32Array.from(positions) : null;
        },
    };
}
