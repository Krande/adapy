// Which objects of a GLB have no colour of their own, read from its JSON without a scene library:
// the three.js-free viewport draws those in the default colour of the appearance settings
// (state/appearanceStore.ts), as the three.js viewport does from its materials. No three.js.

import {isUncoloured} from "@/state/appearanceStore";

const GLB_MAGIC = 0x46546c67;
const CHUNK_JSON = 0x4e4f534a;

interface GltfDoc {
    scene?: number;
    scenes?: {extras?: Record<string, unknown>}[];
    nodes?: {mesh?: number}[];
    meshes?: {primitives?: {material?: number; attributes?: Record<string, number>}[]}[];
    materials?: {pbrMetallicRoughness?: {baseColorFactor?: number[]; baseColorTexture?: unknown}; extensions?: unknown}[];
}

/** An object's file colour says "none": the grey ada writes. */
export const UNCOLOURED_GREY = 1;
/** An object's file colour says "none": black. */
export const UNCOLOURED_BLACK = 2;

export interface GlbUncoloured {
    /** How many objects were marked. */
    count: number;
    /** The scene's extras as the file has them, parsed: a caller that needs them need not parse again. */
    extras: Record<string, unknown> | null;
}

/** 0 when material `index` has a colour of its own, else which "no colour" it is. */
function uncolouredMaterial(doc: GltfDoc, index: number | undefined): number {
    if (index === undefined) return 0;
    const material = doc.materials?.[index];
    const pbr = material?.pbrMetallicRoughness;
    if (!material || material.extensions || pbr?.baseColorTexture) return 0;
    const c = pbr?.baseColorFactor;
    if (!c || c.length < 3 || !isUncoloured(c[0], c[1], c[2])) return 0;
    return c[0] === 0 ? UNCOLOURED_BLACK : UNCOLOURED_GREY;
}

/** What node `node` draws: 0 when any of it has a colour (a material's, or per vertex). */
function uncolouredNode(doc: GltfDoc, node: number): number {
    const primitives = doc.meshes?.[doc.nodes?.[node]?.mesh ?? -1]?.primitives;
    if (!primitives?.length) return 0;
    let kind = 0;
    for (const p of primitives) {
        const k = p.attributes && "COLOR_0" in p.attributes ? 0 : uncolouredMaterial(doc, p.material);
        if (!k) return 0;
        kind = Math.max(kind, k);
    }
    return kind;
}

/** Find the objects of the GLB in `bytes` whose file gives them no colour: `mark(id, kind)` is
 *  called for each, with `UNCOLOURED_GREY` or `UNCOLOURED_BLACK`. `nodeObjects`: its objects are
 *  its nodes (ids are node indices) instead of the draw ranges ada's scene extras name. Null when
 *  the bytes are not a GLB with a JSON chunk. The JSON is parsed once, here. */
export function glbUncoloured(bytes: Uint8Array, nodeObjects: boolean, mark: (id: string, kind: number) => void): GlbUncoloured | null {
    if (bytes.byteLength < 20) return null;
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(16, true) !== CHUNK_JSON) return null;
    const length = Math.min(view.getUint32(12, true), bytes.byteLength - 20);
    let doc: GltfDoc;
    try {
        doc = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + length))) as GltfDoc;
    } catch {
        return null;
    }
    const extras = doc.scenes?.[doc.scene ?? 0]?.extras ?? null;
    let count = 0;
    if (!doc.materials?.length) return {count, extras};
    if (nodeObjects) {
        for (let n = 0; n < (doc.nodes?.length ?? 0); n++) {
            const kind = uncolouredNode(doc, n);
            if (!kind) continue;
            mark(String(n), kind);
            count++;
        }
        return {count, extras};
    }
    // ada's GLBs: each node's draw ranges, by object id, are in the scene extras.
    for (const key in extras ?? {}) {
        if (!key.startsWith("draw_ranges_node")) continue;
        const kind = uncolouredNode(doc, Number(key.slice("draw_ranges_node".length)));
        if (!kind) continue;
        for (const id in extras![key] as Record<string, unknown>) {
            mark(id, kind);
            count++;
        }
    }
    return {count, extras};
}
