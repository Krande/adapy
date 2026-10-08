// Streaming FEA bake under pyodide via wasm_convert.run_stream("fea", …) —
// the entrypoint the browser worker calls for an in-browser .sin artefact-tree
// bake. Bridges a JS range fetcher (node fs.readSync; in the browser a
// synchronous-XHR Range reader) into a Python fetcher and asserts a zip with
// fea.manifest.json + fea.mesh.glb + field blobs comes back.
//
// Streaming the source is what lets a deck too large to download/stage in wasm
// memory bake at all. Defaults to the committed cantilever fixture; set
// EIGEN_SIN to bake a large multi-GB / many-mode deck.
//
// (Beam-solid sidecars need adacpp; this harness installs only numpy+h5py+
// trimesh+pyquaternion, so beam-heavy decks bake without the beam_solids
// sidecar — the production worker's FEM stack adds adacpp.)

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DIST_DIR = path.join(REPO_ROOT, "dist_pyodide");
const SIN =
    process.env.EIGEN_SIN ||
    path.join(REPO_ROOT, "files/fem_files/cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN");

function resolveAdapyWheel() {
    if (process.env.ADAPY_WHEEL) return process.env.ADAPY_WHEEL;
    const wheels = fs.readdirSync(DIST_DIR).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${DIST_DIR}; build the wheel first`);
    return path.join(DIST_DIR, wheels[wheels.length - 1]);
}

(async () => {
    if (!fs.existsSync(SIN)) throw new Error(`missing SIN ${SIN}`);
    const wheelPath = resolveAdapyWheel();
    const sizeGb = fs.statSync(SIN).size / 1e9;
    console.log(`file: ${SIN} (${sizeGb.toFixed(3)} GB)\nadapy wheel: ${wheelPath}`);

    const py = await loadPyodide();
    py.FS.mkdirTree("/dist");
    py.FS.writeFile("/dist/" + path.basename(wheelPath), fs.readFileSync(wheelPath));
    await py.loadPackage(["micropip", "numpy", "h5py", "Pillow"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install("emfs:/dist/" + path.basename(wheelPath), false);

    const fd = fs.openSync(SIN, "r");
    globalThis.hostRead = (offset, length) => {
        const buf = Buffer.allocUnsafe(length);
        const n = fs.readSync(fd, buf, 0, length, offset);
        return new Uint8Array(buf.buffer, buf.byteOffset, n);
    };
    py.globals.set("HOST_SIZE", fs.statSync(SIN).size);

    const t0 = Date.now();
    const out = py.runPython(`
import io, json, zipfile
from js import hostRead
import ada.cadit.wasm_convert as wc

class JsRangeFetcher:
    def __init__(self, size):
        self._size = int(size)
    def size(self):
        return self._size
    def fetch(self, off, length):
        if length <= 0:
            return b""
        return bytes(hostRead(off, length).to_py())
    def close(self):
        pass

def run():
    try:
        zip_bytes = wc.run_stream("fea", "sin", "fea", JsRangeFetcher(HOST_SIZE))
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        return json.dumps({
            "ok": True,
            "zip_bytes": len(zip_bytes),
            "n_files": len(names),
            "has_manifest": "fea.manifest.json" in names,
            "has_mesh": "fea.mesh.glb" in names,
        })
    except Exception as e:
        import traceback
        return json.dumps({"ok": False, "err": type(e).__name__ + ": " + str(e), "tb": traceback.format_exc()[-1800:]})

run()
`);

    const r = JSON.parse(out);
    console.log(JSON.stringify(r, null, 2), `\nelapsed: ${((Date.now() - t0) / 1000).toFixed(1)}s`);
    if (!r.ok) {
        console.error("FAIL — streaming bake raised:\n" + (r.tb || r.err));
        process.exit(1);
    }
    if (!r.has_manifest || !r.has_mesh || r.zip_bytes < 256) {
        console.error(`FAIL — bake zip incomplete (manifest=${r.has_manifest} mesh=${r.has_mesh} bytes=${r.zip_bytes})`);
        process.exit(1);
    }
    console.log(
        `\nOK — streamed SIN → FEA artefact tree under pyodide ` +
        `(${r.n_files} files, ${r.zip_bytes} B zip) via wasm_convert.run_stream("fea").`,
    );
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
