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
// Identical Pyodide-bootstrap strategy to experiments/pyodide-converter
// — kept in sync with that experiment so they share Python plumbing.

const PYODIDE_VERSION = "0.27.7";
const IFC_WASM_WHEEL =
    "https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl";

// adacpp wheel + minimal adapy.cad source closure are baked into the
// SPA dist by deploy/Dockerfile.viewer (stage `adacpp-wheel` + the
// COPY lines after `npm run build:serve`). Vite serves them from the
// SPA root, so relative-to-origin URLs work in production and in dev.
const ADACPP_WHEEL_URL = "/wheels/adacpp.whl";
const ADAPY_SRC_FILES = [
    "ada/__init__.py",
    "ada/cad/__init__.py",
];

importScripts(`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`);

let pyodide = null;
let bootPromise = null;
let ifcStackPromise = null;
let stepStackPromise = null;

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

// Lazy IFC stack — only loaded when an IFC conversion is requested.
async function ensureIfcStack() {
    if (!ifcStackPromise) {
        ifcStackPromise = (async () => {
            log("Installing trimesh + ifcopenshell (WASM wheel)…");
            await pyodide.runPythonAsync(`
                import micropip
                await micropip.install("trimesh")
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

// Lazy STEP stack — installs the adacpp wheel and mounts adapy source.
async function ensureStepStack() {
    if (!stepStackPromise) {
        stepStackPromise = (async () => {
            log("Fetching adacpp wheel…");
            const wheelResp = await fetch(ADACPP_WHEEL_URL);
            if (!wheelResp.ok) {
                throw new Error(`adacpp wheel fetch failed: ${wheelResp.status} ${wheelResp.statusText} (${ADACPP_WHEEL_URL})`);
            }
            const wheelBytes = new Uint8Array(await wheelResp.arrayBuffer());
            pyodide.FS.mkdirTree("/wheels");
            pyodide.FS.writeFile("/wheels/adacpp.whl", wheelBytes);

            log("Mounting adapy source closure…");
            pyodide.FS.mkdirTree("/adapy_src/ada/cad");
            for (const rel of ADAPY_SRC_FILES) {
                const r = await fetch(`/adapy_src/${rel}`);
                if (!r.ok) {
                    throw new Error(`adapy source fetch failed: ${rel} (${r.status})`);
                }
                pyodide.FS.writeFile(`/adapy_src/${rel}`, new Uint8Array(await r.arrayBuffer()));
            }

            log("Installing adacpp + verifying adapy.cad…");
            await pyodide.runPythonAsync(`
                import sys
                import micropip
                await micropip.install("emfs:/wheels/adacpp.whl")
                if "/adapy_src" not in sys.path:
                    sys.path.insert(0, "/adapy_src")
                import ada.cad
                backend = ada.cad.select_backend(prefer="adacpp")
                print(f"step stack ready: backend={backend.name}")
            `);
        })();
    }
    return stepStackPromise;
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

    const result = await pyodide.runPythonAsync(`
import ada.cad

backend = ada.cad.select_backend(prefer="adacpp")
shape = backend.read_step_bytes(bytes(_step_input_bytes))
glb = backend.write_glb_bytes(shape)
del _step_input_bytes
glb
    `);

    const arr = result.toJs({create_proxies: false});
    result.destroy();
    pyodide.globals.delete("_step_input_bytes");
    return arr;
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
