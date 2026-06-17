// Pyodide IFC/STEP -> GLB conversion worker, embedded into the SPA bundle.
//
// Two conversion stacks coexist in one Pyodide instance:
//   - IFC → GLB:  ifcopenshell (wasm wheel) + trimesh
//   - STEP → GLB: adacpp (wasm wheel, OCCT-cross-compiled) via the
//                 adapy.cad CadBackend abstraction
//
// Each stack is lazy-loaded on first request — paying the install cost
// only for the formats the user actually converts. Dispatch happens in
// onmessage based on data.format ("ifc" | "step").
//
// The Pyodide-bootstrap strategy was prototyped in a standalone
// experiments/pyodide-converter PoC and then promoted to this worker;
// keeping the comment here so the genealogy of the bootstrap idiom
// (loadPyodide → micropip.install → import) is discoverable.

// 0.29.4: Python 3.13 + emscripten 4.0.9 + native wasm-EH — the ABI the adacpp
// wheel (cp313/pyodide_2025_0) and the ifcopenshell wasm wheel target. Keep in
// lockstep with adacpp's tools/build_wheel.py tags + pixi.toml.
const PYODIDE_VERSION = "0.29.4";
const IFC_WASM_WHEEL =
    "https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl";

// adacpp wasm wheel + the pure-python adapy wheel are baked into the SPA
// dist by deploy/Dockerfile.viewer and served from the SPA root, so
// relative-to-origin URLs work in production and in dev (`pixi run
// wheel-pyodide` drops the adapy wheel into src/frontend/public/wheels).
//
// Each wheel keeps its PEP 427 filename (micropip parses it to validate
// name/version/platform tags before installing); a small manifest.json
// next to each resolves the filename so we don't hardcode the version.
const ADACPP_MANIFEST_URL = "/wheels/manifest.json"; // {"adacpp": "<file>.whl"}
const ADAPY_MANIFEST_URL = "/wheels/adapy-manifest.json"; // {"adapy": "<file>.whl"}

importScripts(`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`);

let pyodide = null;
let bootPromise = null;
let ifcStackPromise = null;
let stepStackPromise = null;
let satStackPromise = null;
let femStackPromise = null;
let meshStackPromise = null;
let trimeshPromise = null;
let pyquaternionPromise = null;
let adacppWheelPromise = null;
let adapyWheelPromise = null;

function log(message) {
    self.postMessage({type: "log", message});
}

async function bootstrap() {
    log(`Loading Pyodide v${PYODIDE_VERSION}…`);
    pyodide = await loadPyodide({
        indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`,
    });
    // Pillow is loaded here, before ANY `import ada` (and therefore before the
    // first `import trimesh` the real ada init triggers). trimesh decides
    // whether it has image/texture support at import time by probing for PIL;
    // if trimesh is first imported without Pillow present (e.g. a STEP cell
    // importing ada.cad), it caches "no PIL" and every later textured GLB
    // export (SAT/IFC) fails in trimesh._append_material. Loading Pillow up
    // front makes this order-independent across cells in the shared instance.
    log("Loading numpy + Pillow + micropip…");
    await pyodide.loadPackage(["micropip", "numpy", "Pillow"]);
    self.postMessage({type: "ready"});
}

// Shared trimesh install — the IFC and mesh stacks both need it, so
// pay the micropip cost at most once.
async function ensureTrimesh() {
    if (!trimeshPromise) {
        trimeshPromise = (async () => {
            log("Installing trimesh…");
            await pyodide.runPythonAsync(`
                import micropip
                await micropip.install("trimesh")
            `);
        })();
    }
    return trimeshPromise;
}

// Lazy IFC stack — only loaded when an IFC conversion is requested.
async function ensureIfcStack() {
    if (!ifcStackPromise) {
        ifcStackPromise = (async () => {
            await ensureTrimesh();
            log("Installing ifcopenshell (WASM wheel)…");
            await pyodide.runPythonAsync(`
                import micropip
                await micropip.install("${IFC_WASM_WHEEL}")
            `);
            log("Verifying ifc imports…");
            await pyodide.runPythonAsync(`
                import ifcopenshell
                import ifcopenshell.geom
                import trimesh
                print(f"ifcopenshell {ifcopenshell.version}, trimesh {trimesh.__version__}")
            `);
        })();
    }
    return ifcStackPromise;
}

// Lazy mesh stack — trimesh only (obj/stl/ply/gltf/dae/off → glb).
async function ensureMeshStack() {
    if (!meshStackPromise) {
        meshStackPromise = (async () => {
            await ensureTrimesh();
            await pyodide.runPythonAsync(`import trimesh; print(f"trimesh {trimesh.__version__}")`);
        })();
    }
    return meshStackPromise;
}

// Shared pyquaternion install — adapy's core (vector_transforms) needs it
// for the SAT/FEM geometry paths (the CAD-only ada.cad path does not).
async function ensurePyquaternion() {
    if (!pyquaternionPromise) {
        pyquaternionPromise = (async () => {
            log("Installing pyquaternion…");
            await pyodide.runPythonAsync(`
                import micropip
                await micropip.install("pyquaternion")
            `);
        })();
    }
    return pyquaternionPromise;
}

// Resolve a wheel filename via its manifest and stage it on the pyodide FS.
async function fetchWheelToFs(manifestUrl, key) {
    // no-store: the manifest is the indirection that changes when the wheel is
    // rebuilt/rebumped. A browser-cached manifest names a stale wheel (e.g. an
    // old emscripten-3.1.58 build), so micropip then installs an incompatible
    // wheel. Always resolve the filename fresh; the wheel itself is versioned
    // in its filename, so it stays safely cacheable.
    const mResp = await fetch(manifestUrl, {cache: "no-store"});
    if (!mResp.ok) {
        throw new Error(`${key} manifest fetch failed: ${mResp.status} ${mResp.statusText} (${manifestUrl})`);
    }
    const manifest = await mResp.json();
    const filename = manifest[key];
    if (!filename) {
        throw new Error(`${key} manifest missing "${key}" key: ${JSON.stringify(manifest)}`);
    }
    const wheelUrl = `/wheels/${filename}`;
    const wResp = await fetch(wheelUrl);
    if (!wResp.ok) {
        throw new Error(`${key} wheel fetch failed: ${wResp.status} ${wResp.statusText} (${wheelUrl})`);
    }
    pyodide.FS.mkdirTree("/wheels");
    const fsPath = `/wheels/${filename}`;
    pyodide.FS.writeFile(fsPath, new Uint8Array(await wResp.arrayBuffer()));
    return fsPath;
}

// Lazy adacpp wasm wheel install (the OCCT-backed CAD kernel).
async function ensureAdacppWheel() {
    if (!adacppWheelPromise) {
        adacppWheelPromise = (async () => {
            log("Fetching adacpp wasm wheel…");
            const fsPath = await fetchWheelToFs(ADACPP_MANIFEST_URL, "adacpp");
            pyodide.globals.set("_adacpp_wheel_emfs", `emfs:${fsPath}`);
            try {
                await pyodide.runPythonAsync(`
                    import micropip
                    await micropip.install(_adacpp_wheel_emfs)
                `);
            } finally {
                try {
                    pyodide.globals.delete("_adacpp_wheel_emfs");
                } catch (_) {
                    /* already gone — fine */
                }
            }
        })();
    }
    return adacppWheelPromise;
}

// Lazy adapy (pure-python) wheel install. deps=False: the wheel declares
// no deps; each stack provides the wasm-available ones itself
// (numpy/h5py/pydantic/Pillow via loadPackage; trimesh/pyquaternion via
// micropip). Replaces the old hand-mounted 2-file source closure.
async function ensureAdapyWheel() {
    if (!adapyWheelPromise) {
        adapyWheelPromise = (async () => {
            log("Fetching adapy (pyodide) wheel…");
            const fsPath = await fetchWheelToFs(ADAPY_MANIFEST_URL, "adapy");
            pyodide.globals.set("_adapy_wheel_emfs", `emfs:${fsPath}`);
            try {
                await pyodide.runPythonAsync(`
                    import micropip
                    await micropip.install(_adapy_wheel_emfs, deps=False)
                `);
            } finally {
                try {
                    pyodide.globals.delete("_adapy_wheel_emfs");
                } catch (_) {
                    /* already gone — fine */
                }
            }
        })();
    }
    return adapyWheelPromise;
}

// Lazy STEP stack — adacpp kernel + adapy.cad, both from wheels.
async function ensureStepStack() {
    if (!stepStackPromise) {
        stepStackPromise = (async () => {
            // The adapy wheel now ships the real ada/__init__.py, so importing
            // even ada.cad runs the full init, which eagerly imports trimesh +
            // pyquaternion (numpy is already loaded at bootstrap). Provide them
            // before the import or it raises ModuleNotFoundError.
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            log("Verifying adapy.cad backend…");
            await pyodide.runPythonAsync(`
                import ada.cad
                backend = ada.cad.select_backend(prefer="adacpp")
                print(f"step stack ready: backend={backend.name}")
            `);
        })();
    }
    return stepStackPromise;
}

// Lazy SAT stack — adapy's pure-python ACIS parser (ada.from_acis) builds
// ada.geom and tessellates through the adacpp backend; to_gltf needs
// trimesh (+ Pillow for material export) and the SAT parser needs pydantic.
async function ensureSatStack() {
    if (!satStackPromise) {
        satStackPromise = (async () => {
            log("Installing SAT stack (pydantic + Pillow)…");
            await pyodide.loadPackage(["pydantic", "Pillow"]);
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            log("Verifying SAT stack…");
            await pyodide.runPythonAsync(`
                import ada.cad
                ada.cad.select_backend(prefer="adacpp")
                import ada  # ada.from_acis resolved lazily by the pyodide init
                print("sat stack ready")
            `);
        })();
    }
    return satStackPromise;
}

// Lazy FEM stack — streaming FEA result bake (h5py for .rmed/.med; trimesh
// for the mesh GLB; Pillow for material export). adacpp is installed so the
// beam→solid sidecar can build; without it the bake still ships the main
// mesh + fields (beam-solids are skipped).
async function ensureFemStack() {
    if (!femStackPromise) {
        femStackPromise = (async () => {
            // pydantic: ada's FEM-deck import path (ada.from_fem → concept
            // build) pulls it, same as the SAT stack — without it FEM-deck
            // cells fail with ModuleNotFoundError: pydantic.
            log("Installing FEM stack (h5py + pydantic + Pillow)…");
            await pyodide.loadPackage(["h5py", "pydantic", "Pillow"]);
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            log("Verifying FEM stack…");
            await pyodide.runPythonAsync(`
                import ada.cad
                ada.cad.select_backend(prefer="adacpp")
                from ada.fem.results.artefacts import bake_fea_artefacts_from_source
                print("fem stack ready")
            `);
        })();
    }
    return femStackPromise;
}

// ── stack selection (host-specific) + dispatch into adapy ─────────────────
// All conversion LOGIC lives in adapy (ada.cadit.wasm_convert), shipped in the
// pyodide wheel and shared verbatim with the node sweep driver — no duplicated
// Python here. The worker only installs the right packages/wheels for a cell,
// then calls wasm_convert.run.

async function ensureStacks(format, target) {
    const tgt = (target || "glb").toLowerCase();
    if (format === "fea") return ensureFemStack();
    if (format === "fea_glb") return ensureFemStack(); // SIF/SIN result → single GLB
    if (format === "mesh") return ensureMeshStack();
    if (format === "ifc" && tgt === "glb") return ensureIfcStack(); // raw ifcopenshell fast path
    if (format === "step" && tgt === "glb") return ensureStepStack(); // adacpp fast path
    if (format === "fem") {
        await ensureFemStack();
    } else {
        await ensureSatStack();
    }
    if (format === "ifc" || tgt === "ifc") await ensureIfcStack();
}

// Run one conversion: install the stack, hand the bytes to adapy, return the
// output bytes (Uint8Array) for the caller to post back / upload.
async function runConversion(format, bytes, ext, target) {
    await ensureStacks(format, target);
    const e = (ext || "bin").toLowerCase();
    const u8 = new Uint8Array(bytes);
    const inPath = `/tmp/input.${e}`;
    pyodide.FS.writeFile(inPath, u8);
    pyodide.globals.set("_wc_fmt", format);
    pyodide.globals.set("_wc_ext", e);
    pyodide.globals.set("_wc_target", (target || "glb").toLowerCase());
    pyodide.globals.set("_wc_src", inPath);
    try {
        const result = await pyodide.runPythonAsync(`
import ada.cadit.wasm_convert as _wc
_wc.run(_wc_fmt, _wc_ext, _wc_target, _wc_src)
`);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        for (const g of ["_wc_fmt", "_wc_ext", "_wc_target", "_wc_src"]) {
            try { pyodide.globals.delete(g); } catch (_) { /* fine */ }
        }
    }
}

// ── streaming source (range fetch) ────────────────────────────────────────
// For sources too large to stage in wasm memory (multi-GB SIN: the whole file
// would have to fit under the 4 GiB wasm32 ceiling, and >2 GiB can't even be
// one ArrayBuffer), the worker reads the source in ranges on demand instead of
// receiving its bytes. PagedByteSource.fetch is *synchronous* (called deep in
// the decoder), so the range read must be a SYNCHRONOUS XHR — allowed in a Web
// Worker — not async fetch(). This is the browser analogue of the node test's
// fs.readSync fetcher; in production `url` is a presigned / Range-capable URL.

function syncRangeFetch(url, headers) {
    return (offset, length) => {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url, false); // sync: PagedByteSource.fetch is synchronous
        xhr.responseType = "arraybuffer";
        xhr.setRequestHeader("Range", `bytes=${offset}-${offset + length - 1}`);
        if (headers) for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
        xhr.send();
        if (xhr.status !== 206 && xhr.status !== 200) {
            throw new Error(`range fetch ${xhr.status} ${xhr.statusText} for ${url}`);
        }
        return new Uint8Array(xhr.response);
    };
}

function headContentLength(url, headers) {
    const xhr = new XMLHttpRequest();
    xhr.open("HEAD", url, false);
    if (headers) for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.send();
    const len = xhr.getResponseHeader("Content-Length");
    if (len == null) throw new Error(`HEAD ${url} returned no Content-Length; pass size explicitly`);
    return parseInt(len, 10);
}

// Stream a conversion from a range-capable URL. Bridges the sync-XHR range
// reader into a Python fetcher (size()/fetch()) and calls adapy's
// wasm_convert.run_stream — no whole-file buffer crosses into wasm.
async function runConversionStream(format, ext, target, url, size, headers) {
    await ensureStacks(format, target);
    const total = typeof size === "number" && size > 0 ? size : headContentLength(url, headers);
    pyodide.globals.set("_wcs_fetch", syncRangeFetch(url, headers));
    pyodide.globals.set("_wcs_size", total);
    pyodide.globals.set("_wcs_fmt", format);
    pyodide.globals.set("_wcs_ext", (ext || "bin").toLowerCase());
    pyodide.globals.set("_wcs_target", (target || "glb").toLowerCase());
    try {
        const result = await pyodide.runPythonAsync(`
import ada.cadit.wasm_convert as _wc

class _JsRangeFetcher:
    """Range fetcher backed by the host's sync-XHR reader — the in-browser
    fetch path. adapy stays host-agnostic; the js bridge lives only here."""
    def __init__(self, fetch, size):
        self._fetch = fetch
        self._size = int(size)
    def size(self):
        return self._size
    def fetch(self, offset, length):
        if length <= 0:
            return b""
        return bytes(self._fetch(offset, length).to_py())
    def close(self):
        pass

_wc.run_stream(_wcs_fmt, _wcs_ext, _wcs_target, _JsRangeFetcher(_wcs_fetch, _wcs_size))
`);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        for (const g of ["_wcs_fetch", "_wcs_size", "_wcs_fmt", "_wcs_ext", "_wcs_target"]) {
            try {
                pyodide.globals.delete(g);
            } catch (_) {
                /* fine */
            }
        }
    }
}

self.onmessage = async (e) => {
    const data = e.data;
    if (!bootPromise) {
        bootPromise = bootstrap().catch((err) => {
            self.postMessage({type: "error", message: `bootstrap failed: ${err}`});
            throw err;
        });
    }
    try {
        await bootPromise;
    } catch {
        return;
    }

    if (data.type === "prewarm") {
        // Pre-load the CAD stack (adacpp + adapy + OCCT) in the background when
        // the engine is enabled, so the first conversion after toggling on is
        // instant instead of cold-loading on file open. IFC (ifcopenshell, a
        // large wheel) and FEA (h5py) add their extras lazily on first use.
        try {
            await ensureSatStack();
            self.postMessage({type: "log", message: "WASM engine pre-warmed"});
        } catch (err) {
            self.postMessage({type: "log", message: `prewarm failed: ${String(err.message || err)}`});
        }
        return;
    }

    if (data.type === "convert") {
        const reqId = data.reqId;
        // Default to "ifc" so the existing IFC code paths keep working
        // without explicitly tagging the format.
        const format = (data.format || "ifc").toLowerCase();
        // Default to "glb" — the historical single-target behaviour.
        const target = (data.target || "glb").toLowerCase();
        try {
            // One entrypoint: adapy's wasm_convert.run / .run_stream handles
            // every (format, target) — fast paths included. fea returns a zip.
            // data.stream → read the source in ranges (huge SIN) instead of
            // receiving its whole-file bytes.
            const bytes = data.stream
                ? await runConversionStream(format, data.ext, target, data.url, data.size, data.headers)
                : await runConversion(format, data.bytes, data.ext, target);
            // Report the current wasm linear-memory size so the manager can
            // recycle this worker before it approaches the wasm32 ceiling
            // (pyodide never frees heap back to the OS).
            let heap = 0;
            try {
                heap = pyodide._module.HEAP8.length;
            } catch (_) {
                /* heap introspection unavailable — manager falls back to its timeout */
            }
            self.postMessage(
                {type: "result", reqId, bytes, heap},
                [bytes.buffer],
            );
        } catch (err) {
            self.postMessage({
                type: "error",
                reqId,
                message: String(err.message || err),
            });
        }
    }
};
