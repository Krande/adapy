// The adacpp WebGPU renderer (adacpp src/render, built to adacpp_render.{js,wasm}) for the
// WebGPU-only embed: inlined into the bundle as base64 by vite.config.embed-webgpu.ts, so the
// embed stays one file that works from file:// and inside paradoc's single-file report.
import inline from "virtual:adacpp-render-inline";

import type {AdacppModule, GpuDeviceLike, GpuLike, WebRendererApi} from "../src/viewport/adacpp/types";

function decodeBase64(b64: string): Uint8Array {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

/** Why this browser cannot run the embed, or null when it has WebGPU. */
export function webGpuUnavailableReason(): string | null {
    if (typeof navigator === "undefined") return "no browser";
    if (!(navigator as Navigator & {gpu?: GpuLike}).gpu) return "this browser has no WebGPU";
    return null;
}

let modulePromise: Promise<{factory: (opts: object) => Promise<AdacppModule>; compiled: WebAssembly.Module}> | null = null;

/** The module factory and its compiled wasm: decoded and compiled once per page. */
function loadFactory() {
    if (!modulePromise) {
        modulePromise = (async () => {
            const url = URL.createObjectURL(new Blob([decodeBase64(inline.js) as BlobPart], {type: "text/javascript"}));
            try {
                const mod = await import(/* @vite-ignore */ url);
                const compiled = await WebAssembly.compile(decodeBase64(inline.wasm) as BufferSource);
                return {factory: (mod.default ?? mod) as (opts: object) => Promise<AdacppModule>, compiled};
            } finally {
                URL.revokeObjectURL(url);
            }
        })();
        modulePromise.catch(() => {
            modulePromise = null; // a later mount may try again
        });
    }
    return modulePromise;
}

export interface GpuContext {
    module: AdacppModule;
    renderer: WebRendererApi;
    device: GpuDeviceLike;
}

let serial = 0;

/** A renderer drawing into `canvas` (given an id here), on a device of its own. Throws when WebGPU,
 *  an adapter or the module is unavailable: the caller falls back to the figure's poster. */
export async function createRenderer(canvas: HTMLCanvasElement): Promise<GpuContext> {
    const gpu = (navigator as Navigator & {gpu?: GpuLike}).gpu;
    if (!gpu) throw new Error("this browser has no WebGPU");
    // Each step says which one failed: the reason is shown on the poster (and is all a reader on a
    // phone has to go on).
    const adapter = await step("WebGPU adapter request", () => gpu.requestAdapter({powerPreference: "high-performance"}));
    if (!adapter) throw new Error("this browser has WebGPU but gave no adapter (GPU blocklisted or disabled?)");
    const device = await step("WebGPU device request", () =>
        adapter.requestDevice({
            label: "ada-viewer-webgpu",
            requiredLimits: {
                maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
                maxBufferSize: adapter.limits.maxBufferSize,
            },
        }),
    );
    device.onuncapturederror = (ev) => console.error("[ada-viewer-webgpu]", ev.error.message);
    const {factory, compiled} = await step("loading the renderer", loadFactory);
    // A failed instantiation never settles the factory's promise: race it, so the figure falls back.
    let instantiateFailed: (err: unknown) => void = () => {};
    const failed = new Promise<never>((_, reject) => (instantiateFailed = reject));
    const instantiateWasm = (
        imports: WebAssembly.Imports,
        receive: (instance: WebAssembly.Instance, module: WebAssembly.Module) => void,
    ) => {
        void WebAssembly.instantiate(compiled, imports).then((instance) => receive(instance, compiled), instantiateFailed);
        return {};
    };
    const module = await step("starting the renderer", () =>
        Promise.race([factory({instantiateWasm, preinitializedWebGPUDevice: device}), failed]),
    );
    if (!canvas.id) canvas.id = `ada-viewer-webgpu-${++serial}`;
    const renderer = await step("creating the canvas renderer", async () =>
        new module.WebRenderer(`#${canvas.id}`, gpu.getPreferredCanvasFormat()),
    );
    return {module, renderer, device};
}

/** `fn`, its failure prefixed with what was being done. */
async function step<T>(what: string, fn: () => Promise<T>): Promise<T> {
    try {
        return await fn();
    } catch (err) {
        throw new Error(`${what} failed: ${err instanceof Error ? err.message : String(err)}`);
    }
}

/** Copy typed arrays into the module heap, run `fn` with their pointers (0 for null), free them. */
export function withHeap<T>(
    module: AdacppModule,
    arrays: (Float32Array | Uint32Array | null)[],
    fn: (ptrs: number[]) => T,
): T {
    const ptrs = arrays.map((a) => {
        if (!a || a.length === 0) return 0;
        const p = module._malloc(a.byteLength);
        (a instanceof Float32Array ? module.HEAPF32 : module.HEAPU32).set(a, p >> 2);
        return p;
    });
    try {
        return fn(ptrs);
    } finally {
        for (const p of ptrs) if (p) module._free(p);
    }
}

/** Copy `bytes` into the module heap, run `fn` with its pointer, free it. */
export function withBytes<T>(module: AdacppModule, bytes: Uint8Array, fn: (ptr: number) => T): T {
    const p = module._malloc(Math.max(1, bytes.byteLength));
    module.HEAPU8.set(bytes, p);
    try {
        return fn(p);
    } finally {
        module._free(p);
    }
}
