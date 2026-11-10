// Starting the adacpp WebGPU renderer: a GPU device and the wasm module on it. No three.js here, so the
// viewport that mirrors a three.js scene (adacppViewport.ts) and the one that has no three.js at all
// (viewport/native) start the same way.
import {resolveModuleUrl} from "@/utils/wasm/emscriptenLoader";
import type {AdacppModule, GpuAdapterLike, GpuDeviceLike, GpuLike} from "./types";

const MODULE_URL = "/wasm/adacpp_render.js";

/** The renderer inlined as base64 by embed-script.cjs. The single-file viewer opens from
 *  file://, where there is no /wasm to import from. */
interface InlineRenderer {
    js: string;
    wasm: string;
}

function decodeBase64(b64: string): Uint8Array {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

type ModuleFactory = (opts: object) => Promise<AdacppModule>;

/** Start the module. A failed `instantiateWasm` never settles the factory's own promise, so it is raced
 *  against `instantiationFailed`: the caller gets the reason instead of waiting for ever. */
export function startRendererModule(
    factory: ModuleFactory,
    opts: object,
    instantiationFailed: Promise<never> | null,
): Promise<AdacppModule> {
    const started = factory(opts);
    return instantiationFailed ? Promise.race([started, instantiationFailed]) : started;
}

/** The module factory and the options it needs: from the inlined copy when the page has one
 *  (the wasm instantiated through `instantiateWasm`, since a blob-imported module cannot locate its
 *  own and this build reads no `wasmBinary`), else from /wasm. `instantiationFailed` rejects when the
 *  inlined wasm cannot be instantiated. */
async function loadRendererModule(): Promise<{factory: ModuleFactory; opts: object; instantiationFailed: Promise<never> | null}> {
    const inline = (globalThis as {ADACPP_RENDER_INLINE?: InlineRenderer}).ADACPP_RENDER_INLINE;
    if (inline) {
        const url = URL.createObjectURL(new Blob([decodeBase64(inline.js)], {type: "text/javascript"}));
        try {
            const mod = await import(/* @vite-ignore */ url);
            // Compiled here, so a bad module throws to the caller rather than hanging the factory.
            const compiled = await WebAssembly.compile(decodeBase64(inline.wasm) as BufferSource);
            let fail: (err: unknown) => void = () => {};
            const instantiationFailed = new Promise<never>((_, reject) => (fail = reject));
            instantiationFailed.catch(() => {}); // observed by the race in startRendererModule
            const instantiateWasm = (
                imports: WebAssembly.Imports,
                receive: (instance: WebAssembly.Instance, module: WebAssembly.Module) => void,
            ) => {
                void WebAssembly.instantiate(compiled, imports).then(
                    (instance) => receive(instance, compiled),
                    (err) => fail(new Error(`instantiating the inlined renderer failed: ${err instanceof Error ? err.message : String(err)}`)),
                );
                return {};
            };
            return {factory: mod.default ?? mod, opts: {instantiateWasm}, instantiationFailed};
        } finally {
            URL.revokeObjectURL(url);
        }
    }
    const mod = await import(/* @vite-ignore */ resolveModuleUrl(MODULE_URL, globalThis.location?.origin));
    return {factory: mod.default ?? mod, opts: {}, instantiationFailed: null};
}

/** A device for the renderer and the module started on it. Throws, with the reason, when the browser has
 *  no WebGPU, gives no adapter, or the module cannot start. */
export async function startRenderer(label: string): Promise<{gpu: GpuLike; adapter: GpuAdapterLike; device: GpuDeviceLike; module: AdacppModule}> {
    const gpu = (navigator as Navigator & {gpu?: GpuLike}).gpu;
    if (!gpu) throw new Error("this browser has no WebGPU (navigator.gpu is missing)");
    const adapter = await gpu.requestAdapter({powerPreference: "high-performance"});
    if (!adapter) throw new Error("no WebGPU adapter available");
    // Large merged CAD/FEM buffers exceed the 128 MiB default storage binding: take what the adapter has.
    const device = await adapter.requestDevice({
        label,
        requiredLimits: {
            maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
            maxBufferSize: adapter.limits.maxBufferSize,
        },
    });
    const {factory, opts, instantiationFailed} = await loadRendererModule();
    const module = await startRendererModule(factory, {...opts, preinitializedWebGPUDevice: device}, instantiationFailed);
    return {gpu, adapter, device, module};
}
