// Streaming SIN → GLB under pyodide via wasm_convert.run_stream — the exact
// entrypoint the browser worker's runConversionStream calls.
//
// Bridges a JS range fetcher (here node fs.readSync; in the browser a
// synchronous-XHR Range reader) into a Python fetcher (size()/fetch()) and
// runs `wasm_convert.run_stream("fea_glb", "sin", "glb", fetcher)`, asserting
// a valid GLB comes back. Defaults to the committed cantilever fixture; set
// EIGEN_SIN to point at a large multi-GB SIN to exercise the streaming win.

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
    await py.loadPackage(["micropip", "numpy", "Pillow"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install("emfs:/dist/" + path.basename(wheelPath), false);

    // Range fetcher (node fs.readSync handles 64-bit offsets) — the in-node
    // analogue of the worker's synchronous-XHR Range reader.
    const fd = fs.openSync(SIN, "r");
    globalThis.hostRead = (offset, length) => {
        const buf = Buffer.allocUnsafe(length);
        const n = fs.readSync(fd, buf, 0, length, offset);
        return new Uint8Array(buf.buffer, buf.byteOffset, n);
    };
    py.globals.set("HOST_SIZE", fs.statSync(SIN).size);

    const out = py.runPython(`
import json
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
        glb = wc.run_stream("fea_glb", "sin", "glb", JsRangeFetcher(HOST_SIZE))
        return json.dumps({"ok": True, "glb_bytes": len(glb), "is_glb": glb[:4] == b"glTF"})
    except Exception as e:
        import traceback
        return json.dumps({"ok": False, "err": type(e).__name__ + ": " + str(e), "tb": traceback.format_exc()[-1500:]})

run()
`);

    const r = JSON.parse(out);
    console.log(JSON.stringify(r, null, 2));
    if (!r.ok) {
        console.error("FAIL — run_stream raised:\n" + (r.tb || r.err));
        process.exit(1);
    }
    if (!r.is_glb || r.glb_bytes < 64) {
        console.error(`FAIL — output is not a valid GLB (is_glb=${r.is_glb}, bytes=${r.glb_bytes})`);
        process.exit(1);
    }
    console.log(`\nOK — streamed SIN → GLB under pyodide (${r.glb_bytes} bytes) via wasm_convert.run_stream.`);
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
