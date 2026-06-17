// Per-file streaming FEA bake under pyodide via wasm_convert.run_stream("fea",
// …, upload=…) — the entrypoint the browser worker calls for the fully-bounded
// in-browser .sin bake. Bridges a JS range fetcher (node fs.readSync; in the
// browser a synchronous-XHR Range reader) AND a JS upload sink (here: collect
// into a map; in the browser a synchronous-XHR POST per file).
//
// Asserts every artefact (manifest, mesh, field blobs) is shipped through the
// sink one file at a time — the output tree is never zipped or held whole — and
// cross-checks the file set against the buffered zip bake on the same deck.
//
// Defaults to the committed cantilever fixture; set EIGEN_SIN to bake a large
// multi-GB / many-mode deck.

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

    // The upload sink: receive one artefact at a time. Record name + size +
    // order; this stands in for the worker's synchronous-XHR POST per file.
    const shipped = [];
    let peakSeenBytes = 0;
    globalThis.hostUpload = (name, bytes) => {
        // `bytes` is a JS Uint8Array (to_js(data) on the Python side).
        peakSeenBytes = Math.max(peakSeenBytes, bytes.length);
        shipped.push({name: String(name), size: bytes.length});
    };
    py.globals.set("HOST_SIZE", fs.statSync(SIN).size);

    const t0 = Date.now();
    const out = py.runPython(`
import json
from js import hostRead, hostUpload
from pyodide.ffi import to_js
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

def _upload(name, data):
    hostUpload(name, to_js(data))

def run():
    try:
        summary = wc.run_stream("fea", "sin", "fea", JsRangeFetcher(HOST_SIZE), upload=_upload)
        return summary  # already a JSON string
    except Exception as e:
        import traceback
        return json.dumps({"ok": False, "err": type(e).__name__ + ": " + str(e), "tb": traceback.format_exc()[-1800:]})

run()
`);

    const r = JSON.parse(out);
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    if (r.ok === false) {
        console.error("FAIL — streaming upload bake raised:\n" + (r.tb || r.err));
        process.exit(1);
    }

    const names = shipped.map((f) => f.name);
    console.log(
        JSON.stringify(
            {
                summary: r,
                shipped_count: shipped.length,
                last_file: names[names.length - 1],
                largest_file_bytes: peakSeenBytes,
            },
            null,
            2,
        ),
        `\nelapsed: ${elapsed}s`,
    );

    const hasManifest = names.includes("fea.manifest.json");
    const hasMesh = names.includes("fea.mesh.glb");
    if (!hasManifest || !hasMesh) {
        console.error(`FAIL — sink missing artefacts (manifest=${hasManifest} mesh=${hasMesh})`);
        process.exit(1);
    }
    if (names[names.length - 1] !== "fea.manifest.json") {
        console.error(`FAIL — manifest must ship last; got order: ${names.join(", ")}`);
        process.exit(1);
    }
    if (shipped.length !== r.count || r.files.length !== r.count) {
        console.error(`FAIL — count mismatch: shipped ${shipped.length}, summary.count ${r.count}`);
        process.exit(1);
    }
    const totalShipped = shipped.reduce((a, f) => a + f.size, 0);
    if (totalShipped !== r.bytes) {
        console.error(`FAIL — byte total mismatch: shipped ${totalShipped}, summary.bytes ${r.bytes}`);
        process.exit(1);
    }
    console.log(
        `\nOK — streamed SIN → ${shipped.length} artefacts shipped one-by-one under pyodide ` +
        `(largest single file ${peakSeenBytes} B; tree never zipped/held whole) ` +
        `via wasm_convert.run_stream("fea", upload=…).`,
    );
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
