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

// Where Pyodide loads from (and its version) arrives in the first message, the
// boot config the main thread resolves in pyodide_converter.ts: PYODIDE_VERSION
// and the default index live in pluginWheels.ts, next to the deployment's
// PYODIDE_INDEX_URL and a Pyodide runtime the build may stage beside the wheels.
// So pyodide.js is loaded in bootstrap(), not at the top of this file.
//
// This file is a classic worker that the dev server serves unbundled, so it
// imports nothing: install decisions (which baked files, which index packages,
// which PyPI deps) are made on the main thread and arrive as plans.
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
const pluginWheelPromises = new Map();
// Baked wheels for the pure-Python stacks (trimesh, pyquaternion), from the boot
// config: {name: {file, sha256?}}. A stack named here installs from /wheels/;
// otherwise micropip resolves it from Pyodide's index or PyPI, as before.
let runtimeWheels = {};

function log(message) {
    self.postMessage({type: "log", message});
}

// config: {version, indexURL, lockFileContents?, runtimeWheels?} from the boot
// message. lockFileContents is Pyodide's own lock with baked packages pointed at
// /wheels/; Pyodide still resolves their dependencies and checks their sha256.
async function bootstrap(config) {
    if (!config || !config.indexURL) {
        throw new Error("no Pyodide boot config in the first message");
    }
    runtimeWheels = config.runtimeWheels && typeof config.runtimeWheels === "object" ? config.runtimeWheels : {};
    log(`Loading Pyodide v${config.version} from ${config.indexURL}…`);
    importScripts(`${config.indexURL}pyodide.js`);
    const options = {indexURL: config.indexURL};
    if (config.lockFileContents) {
        options.lockFileContents = config.lockFileContents;
        // Without a lock URL Pyodide has no base for the lock's relative file names.
        options.packageBaseUrl = config.indexURL;
    }
    pyodide = await loadPyodide(options);
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

// One pure-Python stack: the wheel the build baked for it (deps=False; numpy,
// its only dependency, is loaded at boot), else micropip by name.
async function installRuntimeWheel(name) {
    const baked = runtimeWheels[name];
    if (baked && baked.file) {
        log(`Installing baked ${baked.file}…`);
        await micropipInstallNoDeps(await stageBakedFile(baked.file, baked.sha256));
        return;
    }
    log(`Installing ${name}…`);
    pyodide.globals.set("_pw_runtime_name", name);
    try {
        await pyodide.runPythonAsync(`
            import micropip
            await micropip.install(_pw_runtime_name)
        `);
    } finally {
        try { pyodide.globals.delete("_pw_runtime_name"); } catch (_) { /* fine */ }
    }
}

// Shared trimesh install — the IFC and mesh stacks both need it, so
// pay the install cost at most once.
async function ensureTrimesh() {
    if (!trimeshPromise) {
        trimeshPromise = installRuntimeWheel("trimesh");
    }
    return trimeshPromise;
}

// ifcopenshell's wasm wheel. Baked (the boot config's runtime table names it): its
// requirements first -- Pyodide lock packages by name (the lock points baked ones at
// /wheels/), then the baked runtime stacks it depends on (isodate, lark) -- and the wheel
// itself with deps=False, so nothing comes from ifcopenshell's index or PyPI. Otherwise
// from ifcopenshell's index, with micropip resolving its requirements, as before.
async function installIfcopenshell() {
    const baked = runtimeWheels.ifcopenshell;
    if (baked && baked.file) {
        if (Array.isArray(baked.pyodide) && baked.pyodide.length) await pyodide.loadPackage(baked.pyodide);
        for (const dep of Array.isArray(baked.depends) ? baked.depends : []) await installRuntimeWheel(dep);
        log(`Installing baked ${baked.file}…`);
        await micropipInstallNoDeps(await stageBakedFile(baked.file, baked.sha256));
        return;
    }
    log("Installing ifcopenshell (WASM wheel)…");
    await pyodide.runPythonAsync(`
        import micropip
        await micropip.install("${IFC_WASM_WHEEL}")
    `);
}

// Lazy IFC stack — only loaded when an IFC conversion is requested.
async function ensureIfcStack() {
    if (!ifcStackPromise) {
        ifcStackPromise = (async () => {
            await ensureTrimesh();
            await installIfcopenshell();
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
        pyquaternionPromise = installRuntimeWheel("pyquaternion");
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
// (numpy/h5py/pydantic/Pillow via loadPackage; trimesh/pyquaternion baked or
// via micropip). Replaces the old hand-mounted 2-file source closure.
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

// Fetch a file baked under /wheels/, check its sha256 when the plan names one,
// and stage it on the pyodide FS. Returns the emfs: URL micropip installs from.
async function stageBakedFile(file, sha256) {
    const url = `/wheels/${encodeURIComponent(file)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
        throw new Error(`baked file fetch failed: ${resp.status} ${resp.statusText} (${url})`);
    }
    const bytes = new Uint8Array(await resp.arrayBuffer());
    if (sha256) {
        const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
        const hex = Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
        if (hex !== sha256) {
            throw new Error(`sha256 mismatch for ${file}: manifest ${sha256}, served ${hex}`);
        }
    }
    pyodide.FS.mkdirTree("/wheels");
    const fsPath = `/wheels/${file}`;
    pyodide.FS.writeFile(fsPath, bytes);
    return `emfs:${fsPath}`;
}

async function micropipInstallNoDeps(emfsUrl) {
    pyodide.globals.set("_pw_wheel_emfs", emfsUrl);
    try {
        await pyodide.runPythonAsync(`
            import micropip
            await micropip.install(_pw_wheel_emfs, deps=False)
        `);
    } finally {
        try { pyodide.globals.delete("_pw_wheel_emfs"); } catch (_) { /* fine */ }
    }
}

// The worker's own stacks a plugin may name in its deps.
async function ensureRuntimeDep(dep) {
    if (dep === "adapy") return ensureAdapyRuntime();
    if (dep === "adacpp") return ensureAdacppWheel();
    if (dep === "ifcopenshell") return ensureIfcStack();
    if (dep === "trimesh") return ensureTrimesh();
    if (dep === "pyquaternion") return ensurePyquaternion();
    throw new Error(`unknown runtime dependency "${dep}"`);
}

// Install one plugin from its plan (pluginWheels.ts planPluginInstall), once per
// worker: a job and the background prewarm asking for the same plugin share the
// install. Order: the worker's stacks, Pyodide packages by name (baked ones come
// from /wheels/ through the boot lock file), baked wheels (deps=False, in order),
// unbaked deps through micropip (Pyodide's index, then PyPI), the plugin wheel
// itself (deps=False), then its register hook.
function ensurePluginWheel(plan) {
    const pluginId = plan.id;
    if (pluginWheelPromises.has(pluginId)) {
        return pluginWheelPromises.get(pluginId);
    }
    const p = (async () => {
        for (const dep of plan.runtime || []) {
            await ensureRuntimeDep(dep);
        }
        const names = plan.pyodidePackages || [];
        if (names.length > 0) {
            log(`Loading Pyodide packages for ${pluginId}: ${names.join(", ")}…`);
            await pyodide.loadPackage(names);
        }
        for (const pkg of plan.bakedWheels || []) {
            log(`Installing baked ${pkg.file} for ${pluginId}…`);
            await micropipInstallNoDeps(await stageBakedFile(pkg.file, pkg.sha256));
        }
        const micropipPkgs = plan.micropip || [];
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
        log(`Fetching plugin wheel ${plan.wheel}…`);
        await micropipInstallNoDeps(await stageBakedFile(plan.wheel, plan.wheelSha256));
        if (plan.register) {
            pyodide.globals.set("_pw_register_spec", plan.register);
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
        return plan;
    })();
    pluginWheelPromises.set(pluginId, p);
    p.catch(() => pluginWheelPromises.delete(pluginId));
    return p;
}

// Install a plugin's plans (the plugins it depends on first, itself last) and
// return its own plan.
async function ensurePluginPlans(pluginId, plans) {
    if (!Array.isArray(plans) || plans.length === 0) {
        throw new Error(`No browser wheel registered in plugin-wheels-manifest.json for "${pluginId}"`);
    }
    let own = null;
    for (const plan of plans) {
        const done = await ensurePluginWheel(plan);
        if (plan.id === pluginId) own = done;
    }
    if (!own) throw new Error(`install plans for "${pluginId}" do not include it`);
    return own;
}

// Background install of the plugins the main thread selected (enabled where the
// user is, Pyodide first in their execution order), one after another so a job
// that starts meanwhile is not starved. A failure is logged and the next plugin
// still installs; a job for the failed one retries the install.
async function prewarmPlugins(plans) {
    for (const plan of plans) {
        try {
            await ensurePluginWheel(plan);
            log(`plugin ${plan.id} pre-installed`);
        } catch (err) {
            log(`plugin ${plan.id} prewarm failed: ${String((err && err.message) || err)}`);
        }
    }
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
async function runPluginJob(reqId, pluginId, plans, scope, options, authHeaders, apiBase, derivedPrefix, entrypointOverride) {
    await ensureAdapyRuntime();
    const entry = await ensurePluginPlans(pluginId, plans);
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
// plans: the engine's install plans when the manifest lists it, else null (a
// built-in engine). The plugin's register hook announces the engine (slug and
// entrypoint) in this adapy, so the compile dispatches by slug as on a worker.
async function runProceduralEngine(engineId, docJson, plans, lod) {
    await ensureProceduralStack();
    if (Array.isArray(plans) && plans.length > 0) {
        await ensurePluginPlans(engineId, plans);
    }
    return compileProcedural(docJson, engineId, null, lod);
}

// openpyxl for the default engine's workbook (a baked plugin's plan brings its own):
// a baked copy (with et-xmlfile, its one dependency) when the boot config names one,
// else micropip by name, which resolves et-xmlfile too.
let openpyxlPromise = null;
async function ensureOpenpyxl() {
    if (!openpyxlPromise) {
        openpyxlPromise = (async () => {
            try {
                await pyodide.runPythonAsync("import openpyxl");
                return;
            } catch (_) {
                /* not installed yet */
            }
            if (runtimeWheels.openpyxl && runtimeWheels["et-xmlfile"]) await installRuntimeWheel("et-xmlfile");
            await installRuntimeWheel("openpyxl");
        })();
        openpyxlPromise.catch(() => {
            openpyxlPromise = null;
        });
    }
    return openpyxlPromise;
}

// A procedural model's Excel export / import, relocation proposals or IFC/Genie
// export (ada.topo_model.wasm_io.run): the procedural stack, the engine's plugin when
// a baked wheel registers it (plans), openpyxl for a workbook, ifcopenshell for IFC.
// Returns bytes: the file, or the UTF-8 JSON answer of import_xlsx / propose_relocations.
async function runProceduralIo(action, payloadJson, data, engineId, plans) {
    await ensureProceduralStack();
    if (Array.isArray(plans) && plans.length > 0) {
        await ensurePluginPlans(engineId, plans);
    }
    if (action === "export_xlsx" || action === "import_xlsx") await ensureOpenpyxl();
    let format = "";
    try {
        format = String(JSON.parse(payloadJson).format || "").toLowerCase();
    } catch (_) {
        /* the Python side reports a bad payload */
    }
    if (action === "export_model" && format === "ifc") await ensureIfcStack();
    pyodide.globals.set("_pio_action", action);
    pyodide.globals.set("_pio_payload", payloadJson);
    pyodide.globals.set("_pio_data", data ? new Uint8Array(data) : new Uint8Array(0));
    try {
        const result = await pyodide.runPythonAsync(`
import ada.topo_model.wasm_io as _pio
_pio_bytes = bytes(_pio_data.to_py())
_pio_out = _pio.run(_pio_action, _pio_payload, _pio_bytes or None)
_pio_out.encode("utf-8") if isinstance(_pio_out, str) else _pio_out
`);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        for (const g of ["_pio_action", "_pio_payload", "_pio_data", "_pio_bytes", "_pio_out"]) {
            try { pyodide.globals.delete(g); } catch (_) { /* fine */ }
        }
    }
}

// The stack a core job runs on (ada.comms.rest.wasm_jobs): adapy with adacpp's kernel and coal
// clash module (pydantic for the connection specs, Pillow for the GLB writer, sqlite3 for the
// readers that import it), ifcopenshell only for a job that reads or writes IFC.
let coreJobStackPromise = null;
async function ensureCoreJobStack() {
    if (!coreJobStackPromise) {
        coreJobStackPromise = (async () => {
            await pyodide.loadPackage(["pydantic", "Pillow", "sqlite3"]);
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            await pyodide.runPythonAsync(`
import ada.cad
ada.cad.select_backend(prefer="adacpp")
import ada.comms.rest.wasm_jobs
`);
        })();
        coreJobStackPromise.catch(() => {
            coreJobStackPromise = null;
        });
    }
    return coreJobStackPromise;
}

// Run a claimed core job (services/compute/coreJobs.ts): the worker handler's own work
// (ada.comms.rest.wasm_jobs.run) on the claim, reading the scope's blobs with synchronous GETs.
// plans: the plugin wheels serving the job's pool (their register hooks add a checker or a spec).
// Answers the status document and the outputs by name; the main thread stores them via the API.
async function runCoreJob(reqId, claimJson, plans, apiBase, authHeaders) {
    await ensureCoreJobStack();
    // ifcopenshell for a job that reads or writes IFC. A run's annotate / detail / subjects and a
    // node or group check name no source in the claim (they rebuild the members from what the
    // run or the manifests say), so they may read an IFC too.
    let claimKind = "";
    try {
        claimKind = String(JSON.parse(claimJson).kind || "");
    } catch (_) {
        /* the Python side reports a bad claim */
    }
    const readsUnnamedSources = ["clash_contacts", "clash_detail", "clash_detail_group", "clash_check_asset", "clash_check_group"];
    if (claimJson.toLowerCase().includes(".ifc") || readsUnnamedSources.includes(claimKind)) await ensureIfcStack();
    for (const plan of Array.isArray(plans) ? plans : []) await ensurePluginWheel(plan);
    const claim = JSON.parse(claimJson);
    const scope = String(claim.scope || "shared");
    const base = (apiBase || "/api").replace(/\/+$/, "");
    pyodide.globals.set("_cj_claim", claimJson);
    pyodide.globals.set("_cj_get_bytes", (key) => syncBlobGet(base, scope, key, authHeaders));
    pyodide.globals.set("_cj_list_keys", (prefix) => {
        const p = String(prefix || "");
        return syncListScopeKeys(base, scope, authHeaders).filter((k) => !p || k.startsWith(p)).sort();
    });
    pyodide.globals.set("_cj_progress", (stage, fraction) =>
        self.postMessage({type: "progress", reqId, message: String(stage || ""), fraction: typeof fraction === "number" ? fraction : 0}),
    );
    try {
        const status = await pyodide.runPythonAsync(`
import pathlib

class _CoreJobStorage:
    """The synchronous facade a worker hands a handler, over the API's blob GETs."""

    def get_bytes(self, key):
        try:
            return bytes(_cj_get_bytes(key).to_py())
        except Exception as exc:
            if "GET 404 " in str(exc):
                raise FileNotFoundError(key) from exc
            raise

    def list_keys(self, prefix=""):
        return list(_cj_list_keys(prefix).to_py())

    def get_many(self, keys):
        out = {}
        for key in keys:
            try:
                out[key] = self.get_bytes(key)
            except (FileNotFoundError, KeyError):
                out[key] = None
        return out

    def fetch_to_path(self, key, dest):
        p = pathlib.Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.get_bytes(key))
        return dest

    def provider_denial(self, key):
        return None

import ada.comms.rest.wasm_jobs as _wj
_cj_status, _cj_outputs = _wj.run_json(_cj_claim, _CoreJobStorage(), lambda s, f=0.0: _cj_progress(str(s), float(f)))
_cj_status
`);
        const outputs = [];
        const proxy = pyodide.globals.get("_cj_outputs");
        try {
            for (const name of proxy.keys()) {
                const value = proxy.get(name);
                const bytes = value && typeof value.toJs === "function" ? value.toJs() : value;
                outputs.push({name: String(name), bytes: new Uint8Array(bytes)});
                if (value && typeof value.destroy === "function") value.destroy();
            }
        } finally {
            proxy.destroy();
        }
        return {status: String(status), outputs};
    } finally {
        for (const g of ["_cj_claim", "_cj_get_bytes", "_cj_list_keys", "_cj_progress", "_cj_status", "_cj_outputs"]) {
            try { pyodide.globals.delete(g); } catch (_) { /* fine */ }
        }
    }
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
async function compileProcedural(docJson, engine, wheel, lod) {
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
    pyodide.globals.set("_pc_lod", lod === "detail" ? "detail" : "sim");
    try {
        const result = await pyodide.runPythonAsync(`
import ada.topo_model.wasm_compile as _pc
_pc.compile_doc(_pc_doc, engine=_pc_engine, lod=_pc_lod)
`);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        try { pyodide.globals.delete("_pc_doc"); } catch (_) { /* fine */ }
        try { pyodide.globals.delete("_pc_engine"); } catch (_) { /* fine */ }
        try { pyodide.globals.delete("_pc_lod"); } catch (_) { /* fine */ }
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
// srcKey: the source's storage key, which an FEA bake records as its manifest's `src` (the
// staged file is /tmp/input.<ext>, whose stem would otherwise stand in for it).
async function runConversion(format, bytes, ext, target, srcKey) {
    await ensureStacks(format, target);
    const e = (ext || "bin").toLowerCase();
    const u8 = new Uint8Array(bytes);
    const inPath = `/tmp/input.${e}`;
    pyodide.FS.writeFile(inPath, u8);
    pyodide.globals.set("_wc_fmt", format);
    pyodide.globals.set("_wc_ext", e);
    pyodide.globals.set("_wc_target", (target || "glb").toLowerCase());
    pyodide.globals.set("_wc_src", inPath);
    pyodide.globals.set("_wc_src_key", srcKey || null);
    try {
        const result = await pyodide.runPythonAsync(`
import ada.cadit.wasm_convert as _wc
_wc.run(_wc_fmt, _wc_ext, _wc_target, _wc_src, src_key=_wc_src_key)
`);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        for (const g of ["_wc_fmt", "_wc_ext", "_wc_target", "_wc_src", "_wc_src_key"]) {
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
async function runFeaBakeStream(ext, url, size, headers, uploadUrlBase, uploadHeaders, srcKey) {
    await ensureStacks("fea", "glb");
    const total = typeof size === "number" && size > 0 ? size : headContentLength(url, headers);
    pyodide.globals.set("_wcs_fetch", syncRangeFetch(url, headers));
    pyodide.globals.set("_wcs_size", total);
    pyodide.globals.set("_wcs_upload", syncArtefactUpload(uploadUrlBase, uploadHeaders));
    pyodide.globals.set("_wcs_ext", (ext || "sin").toLowerCase());
    pyodide.globals.set("_wcs_src_key", srcKey || null);
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

_wc.run_stream("fea", _wcs_ext, "glb", _JsRangeFetcher(_wcs_fetch, _wcs_size), upload=_upload, src_key=_wcs_src_key)
`);
        // run_stream returns a Python str (json.dumps) → a JS string here.
        return typeof result === "string" ? result : String(result);
    } finally {
        for (const g of ["_wcs_fetch", "_wcs_size", "_wcs_upload", "_wcs_ext", "_wcs_src_key"]) {
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
        // The first message is the boot message (pyodide_converter.ts spawnWorker).
        bootPromise = bootstrap(data && data.pyodide).catch((err) => {
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
        // data.plugins: install plans of plugins to pre-install once the core
        // stack is warm (see prewarmPlugins).
        try {
            await ensureSatStack();
            self.postMessage({type: "log", message: "WASM engine pre-warmed"});
        } catch (err) {
            self.postMessage({type: "log", message: `prewarm failed: ${String(err.message || err)}`});
            return;
        }
        if (Array.isArray(data.plugins) && data.plugins.length > 0) {
            await prewarmPlugins(data.plugins);
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
            const bytes = await runProceduralEngine(data.engineId, data.doc, data.plans, data.lod);
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

    if (data.type === "run-procedural-io") {
        const reqId = data.reqId;
        try {
            const bytes = await runProceduralIo(data.action, data.payload, data.data, data.engineId, data.plans);
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

    if (data.type === "run-core-job") {
        const reqId = data.reqId;
        try {
            const {status, outputs} = await runCoreJob(reqId, data.claim, data.plans, data.apiBase, data.authHeaders);
            let heap = 0;
            try {
                heap = pyodide._module.HEAP8.length;
            } catch (_) {
                /* heap introspection unavailable */
            }
            self.postMessage({type: "core-result", reqId, status, outputs, heap}, outputs.map((o) => o.bytes.buffer));
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
                data.plans,
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
                    data.ext, data.url, data.size, data.headers, data.uploadUrl, data.uploadHeaders, data.srcKey,
                );
                bytes = new TextEncoder().encode(summary);
            } else if (data.stream) {
                bytes = await runConversionStream(format, data.ext, target, data.url, data.size, data.headers);
            } else {
                bytes = await runConversion(format, data.bytes, data.ext, target, data.srcKey);
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
