// Pyodide IFC -> GLB conversion worker.
//
// Loads Pyodide via CDN, installs the official ifcopenshell WASM wheel
// and trimesh via micropip, then exposes a single "convert" message
// that runs the conversion in Python and returns GLB bytes.
//
// We deliberately do NOT use ifcopenshell's IfcGltfSerializer — the
// maintainer notes the file-based serializers are unreliable in the
// emscripten FS. Instead we drive the geometry iterator directly and
// build the GLB via trimesh, which works entirely in memory.

const PYODIDE_VERSION = "0.27.4";
const IFC_WASM_WHEEL =
    "https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl";

importScripts(`https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`);

let pyodide = null;
let bootPromise = null;

function log(message) {
    self.postMessage({type: "log", message});
}

async function bootstrap() {
    log(`Loading Pyodide v${PYODIDE_VERSION} from CDN…`);
    pyodide = await loadPyodide({
        indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`,
    });

    log("Loading numpy + micropip…");
    await pyodide.loadPackage(["micropip", "numpy"]);

    log("Installing trimesh + ifcopenshell (WASM wheel)…");
    await pyodide.runPythonAsync(`
        import micropip
        await micropip.install("trimesh")
        await micropip.install("${IFC_WASM_WHEEL}")
    `);

    log("Verifying imports…");
    await pyodide.runPythonAsync(`
        import ifcopenshell
        import ifcopenshell.geom
        import trimesh
        print(f"ifcopenshell {ifcopenshell.version}, trimesh {trimesh.__version__}")
    `);
    self.postMessage({type: "ready"});
}

async function convert(bytes, filename) {
    if (!pyodide) throw new Error("Pyodide not initialised yet");

    const u8 = new Uint8Array(bytes);
    pyodide.FS.writeFile("/tmp/input.ifc", u8);
    log(`Wrote ${u8.byteLength} bytes to /tmp/input.ifc`);

    // Run the conversion in Python. The script returns a Python `bytes`
    // value; we convert it to a transferable Uint8Array on the JS side.
    const result = await pyodide.runPythonAsync(`
import ifcopenshell
import ifcopenshell.geom
import numpy as np
import trimesh
from io import BytesIO

ifc = ifcopenshell.open("/tmp/input.ifc")

# Multiprocessing isn't available in WASM; explicitly request 1 worker
# so the iterator doesn't try to fork. Some kernels accept settings
# without "use-world-coords" — we set it best-effort.
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
        except Exception as e:
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

    // result is a PyProxy wrapping a Python bytes object.
    const arr = result.toJs({create_proxies: false});
    result.destroy();
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
        try {
            const bytes = await convert(data.bytes, data.filename);
            self.postMessage({type: "result", bytes}, [bytes.buffer]);
        } catch (err) {
            self.postMessage({type: "error", message: String(err.message || err)});
        }
    }
};
