// Shared loader for the OCC-free adacpp emscripten ES6 modules served from /wasm (adacpp_step_glb,
// adacpp_glb_diff, …). Each module is MODULARIZE+EXPORT_ES6, so its default export is the factory.
// Use from inside a Web Worker (the modules expect a worker for OPFS / off-main-thread compute).
//
// Adding a new wasm-backed task: build the embind module into public/wasm/, then call
// loadEmscriptenModule<MyApi>("/wasm/<name>.js") from a small worker (see diffConverter.worker.ts).

export async function loadEmscriptenModule<T>(url: string): Promise<T> {
  const mod = await import(/* @vite-ignore */ url);
  const create = (mod.default ?? mod) as () => Promise<T>;
  return await create();
}
