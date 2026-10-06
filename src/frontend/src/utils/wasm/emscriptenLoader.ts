// Shared loader for the OCC-free adacpp emscripten ES6 modules served from /wasm (adacpp_step_glb,
// adacpp_glb_diff, …). Each module is MODULARIZE+EXPORT_ES6, so its default export is the factory.
// Use from inside a Web Worker (the modules expect a worker for OPFS / off-main-thread compute).
//
// Adding a new wasm-backed task: build the embind module into public/wasm/, then call
// loadEmscriptenModule<MyApi>("/wasm/<name>.js") from a small worker (see diffConverter.worker.ts).

/**
 * `url` made absolute against the page's origin when it is root-relative.
 *
 * A worker bundled inline (`?worker&inline`) runs from a `blob:` URL, and a
 * root-relative specifier cannot be resolved against one: `import("/wasm/x.js")`
 * there fails with "Failed to resolve module specifier" before anything is
 * fetched. The beam-solid expander is such a worker, so every compact-baked beam
 * fell back to a line. A blob URL's origin is the page's, so prefixing it
 * reaches the same file a page-level import would.
 */
export function resolveModuleUrl(url: string, origin: string | null | undefined): string {
  if (!url.startsWith("/") || url.startsWith("//")) return url;
  if (!origin || origin === "null") return url;
  return `${origin}${url}`;
}

export async function loadEmscriptenModule<T>(url: string): Promise<T> {
  const origin = (globalThis as {location?: {origin?: string}}).location?.origin;
  const mod = await import(/* @vite-ignore */ resolveModuleUrl(url, origin));
  const create = (mod.default ?? mod) as () => Promise<T>;
  return await create();
}
