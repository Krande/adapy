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
const PLUGIN_WHEELS_MANIFEST_URL = "/wheels/plugin-wheels-manifest.json"; // {"plugins": {...}}

importScripts(`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`);

let pyodide = null;
let bootPromise = null;
let ifcStackPromise = null;
let stepStackPromise = null;
let satStackPromise = null;
let femStackPromise = null;
let meshStackPromise = null;
let proceduralStackPromise = null;
let trimeshPromise = null;
let pyquaternionPromise = null;
let adacppWheelPromise = null;
let adapyWheelPromise = null;
let pluginManifestPromise = null;
const pluginWheelPromises = new Map();

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

// Resolve and cache /wheels/plugin-wheels-manifest.json (no-store on first fetch).
async function loadPluginWheelsManifest() {
    if (!pluginManifestPromise) {
        pluginManifestPromise = (async () => {
            const resp = await fetch(PLUGIN_WHEELS_MANIFEST_URL, {cache: "no-store"});
            if (resp.status === 404) {
                return {plugins: {}};
            }
            if (!resp.ok) {
                throw new Error(
                    `plugin wheels manifest fetch failed: ${resp.status} ${resp.statusText} (${PLUGIN_WHEELS_MANIFEST_URL})`,
                );
            }
            const parsed = await resp.json();
            return parsed && typeof parsed === "object" ? parsed : {plugins: {}};
        })();
    }
    return pluginManifestPromise;
}

// Lazy-install a plugin wheel declared in /wheels/plugin-wheels-manifest.json.
// Resolves well-known runtime deps (adapy, adacpp, pydantic, h5py, sqlite3, scipy),
// sibling plugin wheels declared in the same manifest, and pure-Python PyPI deps.
async function ensurePluginWheel(pluginId, _visiting = new Set()) {
    if (pluginWheelPromises.has(pluginId)) {
        return pluginWheelPromises.get(pluginId);
    }
    if (_visiting.has(pluginId)) {
        throw new Error(`Circular plugin wheel dependency detected for "${pluginId}"`);
    }
    _visiting.add(pluginId);
    const p = (async () => {
        const manifest = await loadPluginWheelsManifest();
        const plugins = manifest.plugins || {};
        const entry = plugins[pluginId];
        if (!entry || !entry.wheel) {
            throw new Error(`No browser wheel registered in plugin-wheels-manifest.json for "${pluginId}"`);
        }
        const deps = Array.isArray(entry.deps) ? entry.deps : [];
        const pyodidePkgs = [];
        const micropipPkgs = [];
        for (const dep of deps) {
            if (dep === "adapy") {
                await ensureAdapyRuntime();
            } else if (dep === "adacpp") {
                await ensureAdacppWheel();
            } else if (["pydantic", "h5py", "sqlite3", "scipy", "Pillow", "numpy"].includes(dep)) {
                pyodidePkgs.push(dep);
            } else if (plugins[dep]) {
                await ensurePluginWheel(dep, _visiting);
            } else {
                micropipPkgs.push(dep);
            }
        }
        if (pyodidePkgs.length > 0) {
            log(`Loading Pyodide packages for ${pluginId}: ${pyodidePkgs.join(", ")}…`);
            await pyodide.loadPackage(pyodidePkgs);
        }
        if (micropipPkgs.length > 0) {
            log(`Installing Python deps for ${pluginId}: ${micropipPkgs.join(", ")}…`);
            pyodide.globals.set("_pw_deps_json", JSON.stringify(micropipPkgs));
            try {
                await pyodide.runPythonAsync(`
                    import json, micropip
                    await micropip.install(json.loads(_pw_deps_json))
                `);
            } finally {
                try { pyodide.globals.delete("_pw_deps_json"); } catch (_) { /* fine */ }
            }
        }
        const wheelUrl = `/wheels/${entry.wheel}`;
        log(`Fetching plugin wheel ${entry.wheel}…`);
        const wResp = await fetch(wheelUrl);
        if (!wResp.ok) {
            throw new Error(`plugin wheel fetch failed: ${wResp.status} ${wResp.statusText} (${wheelUrl})`);
        }
        pyodide.FS.mkdirTree("/wheels");
        const fsPath = `/wheels/${entry.wheel}`;
        pyodide.FS.writeFile(fsPath, new Uint8Array(await wResp.arrayBuffer()));
        pyodide.globals.set("_pw_wheel_emfs", `emfs:${fsPath}`);
        try {
            await pyodide.runPythonAsync(`
                import micropip
                await micropip.install(_pw_wheel_emfs, deps=False)
            `);
        } finally {
            try { pyodide.globals.delete("_pw_wheel_emfs"); } catch (_) { /* fine */ }
        }
        if (entry.register) {
            pyodide.globals.set("_pw_register_spec", entry.register);
            try {
                await pyodide.runPythonAsync(`
                    import importlib
                    _spec = _pw_register_spec
                    if ":" in _spec:
                        _mod_name, _fn_name = _spec.split(":", 1)
                        _mod = importlib.import_module(_mod_name)
                        getattr(_mod, _fn_name)()
                    else:
                        importlib.import_module(_spec)
                `);
            } finally {
                try { pyodide.globals.delete("_pw_register_spec"); } catch (_) { /* fine */ }
            }
        }
        return entry;
    })();
    pluginWheelPromises.set(pluginId, p);
    p.catch(() => pluginWheelPromises.delete(pluginId));
    return p;
}

// Synchronous blob GET / file listing helpers for BrowserStorageFacade.
function syncBlobGet(apiBase, scope, key, headers) {
    const url = `${apiBase}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
    const xhr = new XMLHttpRequest();
    xhr.open("GET", url, false);
    xhr.responseType = "arraybuffer";
    if (headers) for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.send();
    if (xhr.status !== 200) {
        throw new Error(`blob GET ${xhr.status} ${xhr.statusText} for ${key}`);
    }
    return new Uint8Array(xhr.response);
}

function syncListScopeKeys(apiBase, scope, headers) {
    const url = `${apiBase}/scopes/${encodeURIComponent(scope)}/files?include_derived=1`;
    const xhr = new XMLHttpRequest();
    xhr.open("GET", url, false);
    if (headers) for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.send();
    if (xhr.status !== 200) {
        return [];
    }
    try {
        const parsed = JSON.parse(xhr.responseText);
        const files = Array.isArray(parsed.files) ? parsed.files : [];
        const keys = [];
        for (const f of files) {
            if (f && typeof f.key === "string") keys.push(f.key);
            if (f && Array.isArray(f.derived)) {
                for (const d of f.derived) {
                    if (d && typeof d.key === "string") keys.push(d.key);
                }
            }
        }
        return keys;
    } catch (_) {
        return [];
    }
}

function guessContentType(key) {
    const lower = String(key || "").toLowerCase();
    if (lower.endsWith(".json")) return "application/json";
    if (lower.endsWith(".glb")) return "model/gltf-binary";
    if (lower.endsWith(".gltf")) return "model/gltf+json";
    if (lower.endsWith(".txt") || lower.endsWith(".log")) return "text/plain";
    if (lower.endsWith(".csv")) return "text/csv";
    return "application/octet-stream";
}

// Execute a plugin job_entrypoint in Pyodide against BrowserStorageFacade.
// All written outputs are collected in-memory and returned to the main thread
// for local OPFS/memory storage + optional explicit Publish to Scope.
async function runPluginJob(reqId, pluginId, scope, options, authHeaders, apiBase, derivedPrefix, entrypointOverride) {
    await ensureAdapyRuntime();
    const entry = await ensurePluginWheel(pluginId);
    const entrypoint = entrypointOverride || entry.entrypoint;
    if (!entrypoint) {
        throw new Error(`Plugin "${pluginId}" has no job entrypoint in plugin-wheels-manifest.json`);
    }
    const base = (apiBase || "/api").replace(/\/+$/, "");
    const written = new Map();
    const jsGetBytes = (key) => {
        if (written.has(key)) return written.get(key).bytes;
        return syncBlobGet(base, scope, key, authHeaders);
    };
    const jsListKeys = (prefix) => {
        const remote = syncListScopeKeys(base, scope, authHeaders);
        const all = new Set([...remote, ...written.keys()]);
        const p = String(prefix || "");
        return Array.from(all).filter((k) => !p || k.startsWith(p)).sort();
    };
    const jsPutBytes = (key, data, contentEncoding) => {
        const copy = new Uint8Array(data);
        written.set(String(key), {
            key: String(key),
            bytes: copy,
            contentType: guessContentType(key),
            contentEncoding: contentEncoding || null,
        });
    };
    const jsProgress = (message, fraction) => {
        self.postMessage({
            type: "progress",
            reqId,
            message: String(message || ""),
            fraction: typeof fraction === "number" ? fraction : 0,
        });
    };

    pyodide.globals.set("_pj_entrypoint", entrypoint);
    pyodide.globals.set("_pj_scope_str", String(scope || "shared"));
    pyodide.globals.set("_pj_options_json", JSON.stringify(options || {}));
    pyodide.globals.set("_pj_derived_prefix", derivedPrefix || "");
    pyodide.globals.set("_pj_get_bytes", jsGetBytes);
    pyodide.globals.set("_pj_list_keys", jsListKeys);
    pyodide.globals.set("_pj_put_bytes", jsPutBytes);
    pyodide.globals.set("_pj_on_progress", jsProgress);

    try {
        const summaryJson = await pyodide.runPythonAsync(`
import importlib, inspect, json, pathlib, threading
from dataclasses import dataclass
from pyodide.ffi import to_js

@dataclass(frozen=True)
class _BrowserScope:
    kind: str
    id: str | None = None
    def prefix(self) -> str:
        return f"{self.kind}:{self.id}" if self.id else self.kind

def _parse_scope(s: str) -> _BrowserScope:
    if ":" in s:
        kind, sid = s.split(":", 1)
        return _BrowserScope(kind=kind, id=sid or None)
    return _BrowserScope(kind=s or "shared", id=None)

class BrowserStorageFacade:
    """Synchronous StorageFacade matching worker._SyncStorageFacade for Pyodide."""
    def list_keys(self, prefix: str = "") -> list[str]:
        return list(_pj_list_keys(prefix).to_py())

    def get_bytes(self, key: str) -> bytes:
        return bytes(_pj_get_bytes(key).to_py())

    def fetch_to_path(self, key: str, dest):
        p = pathlib.Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.get_bytes(key))
        return dest

    def provider_denial(self, key: str):
        return None

    def put_bytes(self, key: str, data: bytes, content_encoding: str | None = None, pre_compressed: bool = False) -> None:
        raw = bytes(data)
        _pj_put_bytes(key, to_js(raw), content_encoding)

    def put_path(self, key: str, src, content_encoding: str | None = None, pre_compressed: bool = False) -> dict:
        raw = pathlib.Path(src).read_bytes()
        self.put_bytes(key, raw, content_encoding=content_encoding, pre_compressed=pre_compressed)
        return {"key": key, "size": len(raw)}

_mod_name, _fn_name = _pj_entrypoint.split(":", 1)
_fn = getattr(importlib.import_module(_mod_name), _fn_name)
_opts = json.loads(_pj_options_json)
_scope = _parse_scope(_pj_scope_str)
_storage = BrowserStorageFacade()

def _on_prog(stage: str, frac: float = 0.0):
    _pj_on_progress(str(stage), float(frac))

_kwargs = {
    "storage": _storage,
    "scope": _scope,
    "on_progress": _on_prog,
    "derived_prefix": _pj_derived_prefix,
}
try:
    _sig = inspect.signature(_fn)
    if "cancel_event" in _sig.parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in _sig.parameters.values()
    ):
        _kwargs["cancel_event"] = threading.Event()
except (TypeError, ValueError):
    pass

_res = _fn(_opts, **_kwargs)
json.dumps(_res if isinstance(_res, dict) else {"result": _res})
`);
        const summary = JSON.parse(typeof summaryJson === "string" ? summaryJson : String(summaryJson));
        const localArtefacts = Array.from(written.values());
        return {summary, localArtefacts};
    } finally {
        for (const g of [
            "_pj_entrypoint",
            "_pj_scope_str",
            "_pj_options_json",
            "_pj_derived_prefix",
            "_pj_get_bytes",
            "_pj_list_keys",
            "_pj_put_bytes",
            "_pj_on_progress",
        ]) {
            try { pyodide.globals.delete(g); } catch (_) { /* fine */ }
        }
    }
}

// Run a procedural engine registered via plugin-wheels-manifest.json.
async function runProceduralEngine(engineId, docJson) {
    await ensureProceduralStack();
    const manifest = await loadPluginWheelsManifest();
    const plugins = manifest.plugins || {};
    if (plugins[engineId]) {
        await ensurePluginWheel(engineId);
    }
    return compileProcedural(docJson, engineId, null);
}

// Every cell dispatches through `import ada.cadit.wasm_convert`, so the adapy
// wheel is a precondition of all of them, not just the CAD formats. ada's
// __init__ is eager, hence trimesh + pyquaternion first (numpy comes from
// bootstrap). Kept outside the per-format stacks on purpose: formats that need
// no CAD kernel install only their own packages, and previously reached the
// dispatch import with no `ada` on sys.path.
async function ensureAdapyRuntime() {
    await ensureTrimesh();
    await ensurePyquaternion();
    await ensureAdapyWheel();
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
            // sqlite3: reading ANY deck imports ada's FEA results package, whose
            // readers and stores import sqlite3 at module load; Pyodide unvendors
            // it from the standard library, so without it every FEM cell failed
            // with ModuleNotFoundError: sqlite3.
            log("Installing FEM stack (h5py + pydantic + Pillow + sqlite3)…");
            await pyodide.loadPackage(["h5py", "pydantic", "Pillow", "sqlite3"]);
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

// Lazy procedural stack — the built-in adapy procedural engine compiled fully
// in-browser (ada.topo_model.wasm_compile.compile_doc). Same set as the SAT
// stack (pydantic for the topology entities; trimesh for the GLB export;
// adacpp for the OCC-free libtess2 tessellation the compile streams through).
async function ensureProceduralStack() {
    if (!proceduralStackPromise) {
        proceduralStackPromise = (async () => {
            log("Installing procedural stack (pydantic + trimesh + adacpp)…");
            await pyodide.loadPackage(["pydantic", "Pillow"]);
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            log("Verifying procedural stack…");
            await pyodide.runPythonAsync(`
                import ada.cad
                ada.cad.select_backend(prefer="adacpp")
                import ada.topo_model.wasm_compile  # entrypoint resolved
                print("procedural stack ready")
            `);
        })();
    }
    return proceduralStackPromise;
}

// Compile a procedural doc (JSON string) to GLB bytes entirely in the browser.
// engine (optional slug) selects a built-in non-default engine (e.g. echo). wheel
// (optional {entrypoint, deps, url}) is an EXTERNAL engine: micropip-install its
// deps + wheel, then dispatch to its module:callable entrypoint — the same
// ada.topo_model.engines resolution the server uses.
async function compileProcedural(docJson, engine, wheel) {
    await ensureProceduralStack();
    let effectiveEngine = engine || null;
    if (wheel && wheel.url && wheel.entrypoint) {
        log("Installing external engine wheel…");
        pyodide.globals.set("_pc_wheel_url", wheel.url);
        pyodide.globals.set("_pc_wheel_deps_json", JSON.stringify(wheel.deps || []));
        try {
            await pyodide.runPythonAsync(`
import json, micropip
_deps = json.loads(_pc_wheel_deps_json)
if _deps:
    await micropip.install(_deps)
await micropip.install(_pc_wheel_url)
`);
        } finally {
            try { pyodide.globals.delete("_pc_wheel_url"); } catch (_) { /* fine */ }
            try { pyodide.globals.delete("_pc_wheel_deps_json"); } catch (_) { /* fine */ }
        }
        // Dispatch via the wheel's module:callable entrypoint (engines resolves ":").
        effectiveEngine = wheel.entrypoint;
    }
    pyodide.globals.set("_pc_doc", docJson);
    pyodide.globals.set("_pc_engine", effectiveEngine);
    try {
        const result = await pyodide.runPythonAsync(`
import ada.topo_model.wasm_compile as _pc
_pc.compile_doc(_pc_doc, engine=_pc_engine)
`);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        try { pyodide.globals.delete("_pc_doc"); } catch (_) { /* fine */ }
        try { pyodide.globals.delete("_pc_engine"); } catch (_) { /* fine */ }
    }
}

// ── stack selection (host-specific) + dispatch into adapy ─────────────────
// All conversion LOGIC lives in adapy (ada.cadit.wasm_convert), shipped in the
// pyodide wheel and shared verbatim with the node sweep driver — no duplicated
// Python here. The worker only installs the right packages/wheels for a cell,
// then calls wasm_convert.run.

async function ensureStacks(format, target) {
    // Invariant: adapy first, for every format. See ensureAdapyRuntime.
    await ensureAdapyRuntime();
    const tgt = (target || "glb").toLowerCase();
    if (format === "fea") return ensureFemStack();
    if (format === "fea_glb") return ensureFemStack(); // SIF/SIN result → single GLB
    if (format === "mesh") return ensureMeshStack();
    // ifc → glb goes through adapy (from_ifc + to_gltf, tessellated by adacpp) so the
    // GLB carries the scene hierarchy; it falls through to the SAT + IFC stacks below.
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

// Synchronous POST of one baked artefact file — the upload analogue of
// syncRangeFetch. PagedByteSource / the bake sink call into this synchronously
// from inside Python, so it must be a sync XHR (allowed in a Worker). `urlBase`
// already carries ?source=…; we append &name=… per file.
function syncArtefactUpload(urlBase, headers) {
    return (name, bytes) => {
        const sep = urlBase.includes("?") ? "&" : "?";
        const url = `${urlBase}${sep}name=${encodeURIComponent(name)}`;
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url, false);
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        if (headers) for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
        xhr.send(bytes);
        if (xhr.status !== 201 && xhr.status !== 200) {
            throw new Error(`artefact upload ${xhr.status} ${xhr.statusText} for ${name}`);
        }
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

// Stream a FEA bake from a range-capable source URL AND ship each baked
// artefact file straight to the server as it lands — so neither the source nor
// the output tree is ever held whole in wasm memory. Returns the JSON summary
// string from wasm_convert (count + manifest), not a blob: the files are
// already uploaded.
async function runFeaBakeStream(ext, url, size, headers, uploadUrlBase, uploadHeaders) {
    await ensureStacks("fea", "glb");
    const total = typeof size === "number" && size > 0 ? size : headContentLength(url, headers);
    pyodide.globals.set("_wcs_fetch", syncRangeFetch(url, headers));
    pyodide.globals.set("_wcs_size", total);
    pyodide.globals.set("_wcs_upload", syncArtefactUpload(uploadUrlBase, uploadHeaders));
    pyodide.globals.set("_wcs_ext", (ext || "sin").toLowerCase());
    try {
        const result = await pyodide.runPythonAsync(`
import ada.cadit.wasm_convert as _wc
from pyodide.ffi import to_js

class _JsRangeFetcher:
    """Range fetcher backed by the host's sync-XHR reader (see runConversionStream)."""
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

def _upload(name, data):
    # Ship one artefact via the host's sync POST. Any error raises through
    # bake_artefacts and aborts the bake rather than dropping a file silently.
    _wcs_upload(name, to_js(data))

_wc.run_stream("fea", _wcs_ext, "glb", _JsRangeFetcher(_wcs_fetch, _wcs_size), upload=_upload)
`);
        // run_stream returns a Python str (json.dumps) → a JS string here.
        return typeof result === "string" ? result : String(result);
    } finally {
        for (const g of ["_wcs_fetch", "_wcs_size", "_wcs_upload", "_wcs_ext"]) {
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

    if (data.type === "procedural-compile") {
        // Compile a procedural doc to GLB bytes in-browser (the built-in
        // adapy-default engine). data.doc is the cellbuilder commit JSON string.
        const reqId = data.reqId;
        try {
            const bytes = await compileProcedural(data.doc, data.engine, data.wheel);
            let heap = 0;
            try {
                heap = pyodide._module.HEAP8.length;
            } catch (_) {
                /* heap introspection unavailable */
            }
            self.postMessage({type: "result", reqId, bytes, heap}, [bytes.buffer]);
        } catch (err) {
            self.postMessage({type: "error", reqId, message: String(err.message || err)});
        }
        return;
    }

    if (data.type === "run-procedural-engine") {
        const reqId = data.reqId;
        try {
            const bytes = await runProceduralEngine(data.engineId, data.doc);
            let heap = 0;
            try {
                heap = pyodide._module.HEAP8.length;
            } catch (_) {
                /* heap introspection unavailable */
            }
            self.postMessage({type: "result", reqId, bytes, heap}, [bytes.buffer]);
        } catch (err) {
            self.postMessage({type: "error", reqId, message: String(err.message || err)});
        }
        return;
    }

    if (data.type === "run-plugin-job") {
        const reqId = data.reqId;
        try {
            const {summary, localArtefacts} = await runPluginJob(
                reqId,
                data.pluginId,
                data.scope,
                data.options,
                data.authHeaders,
                data.apiBase,
                data.derivedPrefix,
                data.entrypoint,
            );
            let heap = 0;
            try {
                heap = pyodide._module.HEAP8.length;
            } catch (_) {
                /* heap introspection unavailable */
            }
            const transfer = localArtefacts.map((a) => a.bytes.buffer);
            self.postMessage({type: "plugin-result", reqId, summary, localArtefacts, heap}, transfer);
        } catch (err) {
            self.postMessage({type: "error", reqId, message: String(err.message || err)});
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
            // receiving its whole-file bytes. data.uploadUrl (+ stream) → also
            // ship each baked artefact to the server as it lands, so the output
            // tree is never held whole; the JSON summary comes back as bytes.
            let bytes;
            if (data.stream && data.uploadUrl) {
                const summary = await runFeaBakeStream(
                    data.ext, data.url, data.size, data.headers, data.uploadUrl, data.uploadHeaders,
                );
                bytes = new TextEncoder().encode(summary);
            } else if (data.stream) {
                bytes = await runConversionStream(format, data.ext, target, data.url, data.size, data.headers);
            } else {
                bytes = await runConversion(format, data.bytes, data.ext, target);
            }
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
