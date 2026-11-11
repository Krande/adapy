// One buffer view of a GLB as bytes, read without a scene library: what data kept beside the
// geometry (a list of ids an extension refers to by buffer view) is read from. No three.js.

const GLB_MAGIC = 0x46546c67;
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

/** Reads buffer views of `bytes` by index: the bytes of each (a view into `bytes`, not a copy), or
 *  null when the file is not a GLB or has no such view in its own binary chunk. The JSON chunk is
 *  parsed on the first call, so a caller that never asks pays nothing. */
export function glbBufferViews(bytes: Uint8Array): (index: number) => Uint8Array | null {
    let views: {buffer?: number; byteOffset?: number; byteLength: number}[] | null = null;
    let bin: Uint8Array | null = null;
    let read = false;
    const open = (): void => {
        read = true;
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        if (bytes.byteLength < 20 || view.getUint32(0, true) !== GLB_MAGIC) return;
        for (let offset = 12; offset + 8 <= bytes.byteLength; ) {
            const length = view.getUint32(offset, true);
            const type = view.getUint32(offset + 4, true);
            const chunk = bytes.subarray(offset + 8, offset + 8 + length);
            if (type === CHUNK_JSON) views = (JSON.parse(new TextDecoder().decode(chunk)) as {bufferViews?: typeof views}).bufferViews ?? null;
            else if (type === CHUNK_BIN && !bin) bin = chunk;
            offset += 8 + length;
        }
    };
    return (index) => {
        if (!read) open();
        const v = views?.[index];
        if (!v || !bin || (v.buffer ?? 0) !== 0) return null;
        const start = v.byteOffset ?? 0;
        return start + v.byteLength <= bin.byteLength ? bin.subarray(start, start + v.byteLength) : null;
    };
}

/** Whether a GLB's JSON names at least one animation. Found by looking for the key in the JSON
 *  text, without parsing it: the question is asked of every model that is loaded, and the text of a
 *  large model is megabytes. A file that only mentions the key in a string is answered true, which
 *  costs a caller nothing but the asking. False for anything that is not a GLB. */
export function glbNamesAnimations(bytes: Uint8Array): boolean {
    if (bytes.byteLength < 20) return false;
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(16, true) !== CHUNK_JSON) return false;
    const length = Math.min(view.getUint32(12, true), bytes.byteLength - 20);
    return /"animations"\s*:\s*\[\s*\{/.test(new TextDecoder().decode(bytes.subarray(20, 20 + length)));
}
