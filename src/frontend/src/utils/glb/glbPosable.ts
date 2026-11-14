// A GLB made ready for its nodes to be placed from outside on the three.js-free viewport.
//
// That viewport's renderer merges the nodes of a file into as few instances as it can, and keeps a
// node an instance of its own only when one of the file's animations moves it. A model whose nodes
// are to be placed by the viewer (viewport/api/modelPose.ts) has no such animation: nothing has been
// recorded yet. So one is added here that names every node and moves none -- each node's own
// translation, twice -- which makes every node an instance without changing where anything is.
// The viewport never plays it.
//
// What comes back beside the bytes is what placing needs and the renderer does not say: each node's
// name, its parent, and where it rests in the file's coordinates. No three.js in this module.
import {fromGltfNode, multiply4} from "@/viewport/api/mat4";

const GLB_MAGIC = 0x46546c67;
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;
const FLOAT = 5126;

export const REST_ANIMATION = "ada:rest";

interface GltfNode {
    name?: string;
    children?: number[];
    matrix?: number[];
    translation?: number[];
    rotation?: number[];
    scale?: number[];
}

interface Gltf {
    nodes?: GltfNode[];
    buffers?: {byteLength: number; uri?: string}[];
    bufferViews?: {buffer: number; byteOffset?: number; byteLength: number}[];
    accessors?: Record<string, unknown>[];
    animations?: Record<string, unknown>[];
}

export interface PosableGlb {
    /** The file with the animation that keeps its nodes apart. */
    bytes: Uint8Array;
    /** Per node of the file: its name ("" when it has none). */
    names: string[];
    /** Per node: its parent's index, -1 for one that has none. */
    parents: Int32Array;
    /** Per node: where it rests in the file's coordinates, sixteen numbers each, column-major. */
    rest: Float64Array;
    /** Per node: whether the added animation names it. One the file places by a `matrix` is left
     *  out (an animated node may not have one), and stays merged with what does not move. */
    apart: boolean[];
}

function chunks(bytes: Uint8Array): {json: Uint8Array; bin: Uint8Array | null} | null {
    if (bytes.byteLength < 20) return null;
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (view.getUint32(0, true) !== GLB_MAGIC) return null;
    let json: Uint8Array | null = null;
    let bin: Uint8Array | null = null;
    for (let offset = 12; offset + 8 <= bytes.byteLength; ) {
        const length = view.getUint32(offset, true);
        const type = view.getUint32(offset + 4, true);
        const chunk = bytes.subarray(offset + 8, offset + 8 + length);
        if (type === CHUNK_JSON && !json) json = chunk;
        else if (type === CHUNK_BIN && !bin) bin = chunk;
        offset += 8 + length;
    }
    return json ? {json, bin} : null;
}

const pad4 = (n: number): number => (n + 3) & ~3;

/** `bytes` with every node kept apart for placing, and what placing needs to know of the nodes.
 *  Null for anything that is not a GLB with nodes, or whose first buffer is not its binary chunk. */
export function makeGlbPosable(bytes: Uint8Array): PosableGlb | null {
    const parts = chunks(bytes);
    if (!parts) return null;
    let gltf: Gltf;
    try {
        gltf = JSON.parse(new TextDecoder().decode(parts.json)) as Gltf;
    } catch {
        return null;
    }
    const nodes = gltf.nodes ?? [];
    if (!nodes.length) return null;
    if (gltf.buffers?.length && (gltf.buffers[0].uri !== undefined || !parts.bin)) return null;

    // The nodes as a tree, and where each rests.
    const parents = new Int32Array(nodes.length).fill(-1);
    nodes.forEach((node, index) => {
        for (const child of node.children ?? []) if (child >= 0 && child < nodes.length && child !== index) parents[child] = index;
    });
    const rest = new Float64Array(nodes.length * 16);
    const done = new Uint8Array(nodes.length);
    const place = (index: number, depth: number): void => {
        if (done[index]) return;
        done[index] = 1;
        const own = fromGltfNode(nodes[index]);
        const parent = parents[index];
        if (parent >= 0 && depth < nodes.length) {
            place(parent, depth + 1);
            multiply4(own, rest, own, parent * 16, 0);
        }
        rest.set(own, index * 16);
    };
    for (let i = 0; i < nodes.length; i++) place(i, 0);

    // The animation: one input (two times) for all, and each node's own translation twice.
    const apart = nodes.map((node) => node.matrix === undefined);
    const moved = apart.reduce((n, yes) => n + (yes ? 1 : 0), 0);
    if (!moved) return {bytes, names: nodes.map((n) => n.name ?? ""), parents, rest, apart};
    const added = new Float32Array(2 + moved * 6);
    added[0] = 0;
    added[1] = 1;
    const binBefore = pad4(parts.bin?.byteLength ?? 0);
    const bufferViews = (gltf.bufferViews ??= []);
    const accessors = (gltf.accessors ??= []);
    const timesView = bufferViews.push({buffer: 0, byteOffset: binBefore, byteLength: 8}) - 1;
    const valuesView = bufferViews.push({buffer: 0, byteOffset: binBefore + 8, byteLength: moved * 24}) - 1;
    const input = accessors.push({bufferView: timesView, componentType: FLOAT, count: 2, type: "SCALAR", min: [0], max: [1]}) - 1;
    const samplers: Record<string, unknown>[] = [];
    const channels: Record<string, unknown>[] = [];
    let slot = 0;
    nodes.forEach((node, index) => {
        if (!apart[index]) return;
        const t = node.translation ?? [0, 0, 0];
        added.set([t[0], t[1], t[2], t[0], t[1], t[2]], 2 + slot * 6);
        const output = accessors.push({bufferView: valuesView, byteOffset: slot * 24, componentType: FLOAT, count: 2, type: "VEC3"}) - 1;
        samplers.push({input, output, interpolation: "STEP"});
        channels.push({sampler: samplers.length - 1, target: {node: index, path: "translation"}});
        slot++;
    });
    (gltf.animations ??= []).push({name: REST_ANIMATION, samplers, channels});
    const binLength = binBefore + added.byteLength;
    if (gltf.buffers?.length) gltf.buffers[0].byteLength = binLength;
    else gltf.buffers = [{byteLength: binLength}];

    // The file again: header, JSON chunk (padded with spaces), binary chunk (padded with zeros).
    const text = new TextEncoder().encode(JSON.stringify(gltf));
    const jsonLength = pad4(text.byteLength);
    const out = new Uint8Array(12 + 8 + jsonLength + 8 + binLength);
    const view = new DataView(out.buffer);
    view.setUint32(0, GLB_MAGIC, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, out.byteLength, true);
    view.setUint32(12, jsonLength, true);
    view.setUint32(16, CHUNK_JSON, true);
    out.set(text, 20);
    out.fill(0x20, 20 + text.byteLength, 20 + jsonLength);
    const binAt = 20 + jsonLength;
    view.setUint32(binAt, binLength, true);
    view.setUint32(binAt + 4, CHUNK_BIN, true);
    if (parts.bin) out.set(parts.bin, binAt + 8);
    out.set(new Uint8Array(added.buffer), binAt + 8 + binBefore);
    return {bytes: out, names: nodes.map((n) => n.name ?? ""), parents, rest, apart};
}
