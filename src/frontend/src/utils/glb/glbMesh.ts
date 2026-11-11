// The first mesh of a GLB as typed arrays, read without a scene library.
//
// An FEA result's mesh GLB is geometry only: one primitive whose vertices are the model's nodes, in
// node order, which is the numbering every field of the result is stored in. Reading it here keeps
// that numbering as it is in the file (no welding, no merging), which a result layer needs to paint
// values onto it. No three.js in this module.
//
// Reads what the bake writes: an uncompressed binary glTF 2.0 with float positions. A primitive
// compressed with an extension (Draco, meshopt) is refused with that reason.

export interface GlbMesh {
    /** xyz per vertex, in the primitive's own axes and order. */
    positions: Float32Array;
    /** Corner indices, or null when the primitive has none. */
    indices: Uint32Array | null;
    topology: "triangles" | "lines" | "points";
    /** Where the mesh's node stands in the scene: a column-major 4x4. */
    matrix: Float32Array;
}

const GLB_MAGIC = 0x46546c67; // "glTF"
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

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
interface Node {
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
    meshes?: {primitives: {attributes: Record<string, number>; indices?: number; mode?: number; extensions?: Record<string, unknown>}[]}[];
    nodes?: Node[];
    scenes?: {nodes?: number[]}[];
    scene?: number;
}

const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

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
function localMatrix(node: Node): number[] {
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

/** The world matrix of the first node (scene order, parents first) that draws mesh `mesh`. */
function meshNodeMatrix(gltf: Gltf, mesh: number): number[] {
    const nodes = gltf.nodes ?? [];
    const roots = gltf.scenes?.[gltf.scene ?? 0]?.nodes ?? nodes.map((_, i) => i);
    const stack: {node: number; parent: number[]}[] = roots.map((node) => ({node, parent: IDENTITY})).reverse();
    const seen = new Set<number>();
    while (stack.length) {
        const {node, parent} = stack.pop()!;
        const n = nodes[node];
        if (!n || seen.has(node)) continue;
        seen.add(node);
        const world = multiply(parent, localMatrix(n));
        if (n.mesh === mesh) return world;
        for (const child of [...(n.children ?? [])].reverse()) stack.push({node: child, parent: world});
    }
    return IDENTITY;
}

const COMPONENTS: Record<string, number> = {SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4};

/** Read the first primitive of the first mesh. Throws, with the reason, on anything else than an
 *  uncompressed GLB with float positions. */
export function readGlbMesh(bytes: Uint8Array): GlbMesh {
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
    const meshIndex = gltf.meshes?.findIndex((m) => m.primitives?.length > 0) ?? -1;
    if (meshIndex < 0) throw new Error("the GLB has no mesh");
    const primitive = gltf.meshes![meshIndex].primitives[0];
    const compressed = Object.keys(primitive.extensions ?? {}).find((e) => /draco|meshopt/i.test(e));
    if (compressed) throw new Error(`the GLB's mesh is compressed (${compressed})`);
    const mode = primitive.mode ?? 4;
    const topology = mode === 4 ? "triangles" : mode === 1 ? "lines" : mode === 0 ? "points" : null;
    if (!topology) throw new Error(`the GLB's mesh has a primitive mode (${mode}) that is not triangles, lines or points`);

    /** An accessor's values as numbers, `components` per element, wherever its view puts them. */
    const read = (index: number, components: number): {count: number; at: (element: number, component: number) => number; packed: Uint8Array | null; componentType: number} => {
        const accessor = gltf!.accessors?.[index];
        if (!accessor || accessor.bufferView === undefined || accessor.sparse) throw new Error("the GLB's mesh has an accessor without data");
        if (COMPONENTS[accessor.type] !== components) throw new Error(`the GLB's mesh has a ${accessor.type} accessor where ${components} components are needed`);
        const bufferView = gltf!.bufferViews?.[accessor.bufferView];
        if (!bufferView || bufferView.buffer !== 0 || !bin) throw new Error("the GLB's mesh data is not in its binary chunk");
        const size = accessor.componentType === 5126 || accessor.componentType === 5125 ? 4 : accessor.componentType === 5123 || accessor.componentType === 5122 ? 2 : 1;
        const stride = bufferView.byteStride || size * components;
        const start = bin.byteOffset + (bufferView.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
        const data = new DataView(bin.buffer, start, (accessor.count - 1) * stride + size * components);
        const get =
            accessor.componentType === 5126 ? (o: number) => data.getFloat32(o, true)
            : accessor.componentType === 5125 ? (o: number) => data.getUint32(o, true)
            : accessor.componentType === 5123 ? (o: number) => data.getUint16(o, true)
            : accessor.componentType === 5121 ? (o: number) => data.getUint8(o)
            : null;
        if (!get) throw new Error(`the GLB's mesh has an accessor of component type ${accessor.componentType}`);
        // Tightly packed data can be copied as bytes instead of read value by value.
        const packed = stride === size * components ? new Uint8Array(bin.buffer, start, accessor.count * stride) : null;
        return {count: accessor.count, at: (element, component) => get(element * stride + component * size), packed, componentType: accessor.componentType};
    };

    const positionAccessor = primitive.attributes?.POSITION;
    if (positionAccessor === undefined) throw new Error("the GLB's mesh has no positions");
    const p = read(positionAccessor, 3);
    if (p.componentType !== 5126) throw new Error("the GLB's mesh positions are not floats");
    const positions = new Float32Array(p.count * 3);
    if (p.packed) new Uint8Array(positions.buffer).set(p.packed);
    else for (let i = 0; i < p.count; i++) for (let c = 0; c < 3; c++) positions[i * 3 + c] = p.at(i, c);

    let indices: Uint32Array | null = null;
    if (primitive.indices !== undefined) {
        const ix = read(primitive.indices, 1);
        indices = new Uint32Array(ix.count);
        if (ix.packed && ix.componentType === 5125) new Uint8Array(indices.buffer).set(ix.packed);
        else for (let i = 0; i < ix.count; i++) indices[i] = ix.at(i, 0);
    }
    return {positions, indices, topology, matrix: Float32Array.from(meshNodeMatrix(gltf, meshIndex))};
}
