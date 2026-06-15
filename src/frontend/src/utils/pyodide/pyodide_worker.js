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

const PYODIDE_VERSION = "0.27.7";
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
    log("Loading numpy + micropip…");
    await pyodide.loadPackage(["micropip", "numpy"]);
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
    const mResp = await fetch(manifestUrl);
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
            log("Installing FEM stack (h5py + Pillow)…");
            await pyodide.loadPackage(["h5py", "Pillow"]);
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

async function convertIfc(bytes) {
    await ensureIfcStack();

    const u8 = new Uint8Array(bytes);
    pyodide.FS.writeFile("/tmp/input.ifc", u8);
    log(`Wrote ${u8.byteLength} bytes to /tmp/input.ifc`);

    const result = await pyodide.runPythonAsync(`
import ifcopenshell
import ifcopenshell.geom
import numpy as np
import trimesh
from io import BytesIO

ifc = ifcopenshell.open("/tmp/input.ifc")
settings = ifcopenshell.geom.settings()
try:
    settings.set("use-world-coords", True)
except Exception:
    pass

iterator = ifcopenshell.geom.iterator(settings, ifc)
scene = trimesh.Scene()
n_meshes = 0
n_skipped = 0
n_errors = 0

if iterator.initialize():
    while True:
        try:
            shape = iterator.get()
            geom = shape.geometry
            verts = np.asarray(geom.verts, dtype=np.float32).reshape(-1, 3)
            faces = np.asarray(geom.faces, dtype=np.int32).reshape(-1, 3)
            if faces.size == 0:
                n_skipped += 1
            else:
                node_name = (getattr(shape, "name", None) or shape.guid or f"mesh_{n_meshes}")
                mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                scene.add_geometry(mesh, node_name=node_name)
                n_meshes += 1
        except Exception:
            n_errors += 1
        if not iterator.next():
            break

print(f"meshes={n_meshes} skipped={n_skipped} errors={n_errors}")
if n_meshes == 0:
    raise RuntimeError("no meshable geometry produced from this IFC")

buf = BytesIO()
scene.export(buf, file_type="glb")
buf.getvalue()
    `);

    const arr = result.toJs({create_proxies: false});
    result.destroy();
    return arr;
}

async function convertStep(bytes) {
    await ensureStepStack();

    const u8 = new Uint8Array(bytes);
    // Push the bytes into the Python heap via globals so we don't have
    // to round-trip through the FS twice (adacpp's read_step_bytes
    // accepts a bytes buffer directly and writes its own MEMFS temp).
    pyodide.globals.set("_step_input_bytes", u8);
    log(`Forwarded ${u8.byteLength} bytes of STEP into Python`);

    try {
        const result = await pyodide.runPythonAsync(`
import ada.cad

backend = ada.cad.select_backend(prefer="adacpp")
shape = backend.read_step_bytes(bytes(_step_input_bytes))
glb = backend.write_glb_bytes(shape)
glb
        `);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        // Single point of cleanup. The Python snippet used to also
        // ``del _step_input_bytes``, but that left the JS-side
        // ``globals.delete`` to throw KeyError on the success path
        // ("KeyError: '_step_input_bytes'"). Owning cleanup on the JS
        // side keeps both success and exception paths symmetrical.
        try {
            pyodide.globals.delete("_step_input_bytes");
        } catch (_) {
            /* already gone — fine */
        }
    }
}

const MESH_EXTS = new Set(["obj", "stl", "ply", "gltf", "dae", "off"]);

async function convertMesh(bytes, ext) {
    await ensureMeshStack();
    const e = (ext || "").toLowerCase();
    if (!MESH_EXTS.has(e)) {
        throw new Error(`unsupported mesh extension for pyodide conversion: ${ext}`);
    }
    const u8 = new Uint8Array(bytes);
    pyodide.FS.writeFile(`/tmp/input.${e}`, u8);
    log(`Wrote ${u8.byteLength} bytes to /tmp/input.${e}`);
    pyodide.globals.set("_mesh_ext", e);

    try {
        const result = await pyodide.runPythonAsync(`
import trimesh
from io import BytesIO

ext = _mesh_ext
loaded = trimesh.load("/tmp/input." + ext, file_type=ext, process=False)
# Normalise to a Scene so single-mesh and multi-mesh inputs export uniformly.
scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
if len(scene.geometry) == 0:
    raise RuntimeError("no meshable geometry produced from this file")
buf = BytesIO()
scene.export(buf, file_type="glb")
buf.getvalue()
        `);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        try {
            pyodide.globals.delete("_mesh_ext");
        } catch (_) {
            /* already gone — fine */
        }
    }
}

// .sat / .acis → GLB. adapy's pure-python ACIS parser (ada.from_acis)
// builds ada.geom; Part.to_gltf tessellates through the adacpp backend and
// trimesh-exports the GLB.
async function convertSat(bytes) {
    await ensureSatStack();
    const u8 = new Uint8Array(bytes);
    pyodide.FS.writeFile("/tmp/input.sat", u8);
    log(`Wrote ${u8.byteLength} bytes to /tmp/input.sat`);

    const result = await pyodide.runPythonAsync(`
import io
import ada

asm = ada.from_acis("/tmp/input.sat")
buf = io.BytesIO()
asm.to_gltf(buf)
glb = buf.getvalue()
if not glb:
    raise RuntimeError("SAT produced an empty GLB")
glb
    `);
    const arr = result.toJs({create_proxies: false});
    result.destroy();
    return arr;
}

const FEA_EXTS = new Set(["rmed", "med", "sif", "sin"]);

// FEA result (.rmed/.med/.sif/.sin) → streaming-viewer artefact tree, baked
// in-browser and returned as a zip. The pipeline POSTs the zip to
// /fea/artefacts, which unpacks it under _derived/<source>.fea/ with the
// same gzip policy the worker uses.
async function bakeFea(bytes, ext) {
    await ensureFemStack();
    const e = (ext || "").toLowerCase();
    if (!FEA_EXTS.has(e)) {
        throw new Error(`unsupported FEA extension for pyodide bake: ${ext}`);
    }
    const u8 = new Uint8Array(bytes);
    pyodide.FS.writeFile(`/tmp/input.${e}`, u8);
    log(`Wrote ${u8.byteLength} bytes to /tmp/input.${e}`);
    pyodide.globals.set("_fea_ext", e);

    try {
        const result = await pyodide.runPythonAsync(`
import io, os, shutil, zipfile
from ada.fem.results.artefacts import bake_fea_artefacts_from_source

ext = _fea_ext
out_dir = "/tmp/fea_out"
if os.path.exists(out_dir):
    shutil.rmtree(out_dir)
os.makedirs(out_dir, exist_ok=True)

bake_fea_artefacts_from_source(f"/tmp/input.{ext}", out_dir)

names = sorted(n for n in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, n)))
if "fea.manifest.json" not in names:
    raise RuntimeError("FEA bake produced no manifest")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    for n in names:
        zf.write(os.path.join(out_dir, n), arcname=n)
buf.getvalue()
        `);
        const arr = result.toJs({create_proxies: false});
        result.destroy();
        return arr;
    } finally {
        try {
            pyodide.globals.delete("_fea_ext");
        } catch (_) {
            /* already gone — fine */
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

    if (data.type === "convert") {
        const reqId = data.reqId;
        // Default to "ifc" so the existing IFC code paths keep working
        // without explicitly tagging the format.
        const format = (data.format || "ifc").toLowerCase();
        try {
            let bytes;
            if (format === "ifc") {
                bytes = await convertIfc(data.bytes);
            } else if (format === "step" || format === "stp") {
                bytes = await convertStep(data.bytes);
            } else if (format === "mesh") {
                bytes = await convertMesh(data.bytes, data.ext);
            } else if (format === "sat") {
                bytes = await convertSat(data.bytes);
            } else if (format === "fea") {
                bytes = await bakeFea(data.bytes, data.ext);
            } else {
                throw new Error(`unsupported format for pyodide conversion: ${format}`);
            }
            self.postMessage(
                {type: "result", reqId, bytes},
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
