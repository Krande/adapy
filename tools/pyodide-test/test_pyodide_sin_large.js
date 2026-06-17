// Large-SIN streaming read under real pyodide (the >2 GiB browser case).
//
// A multi-GB SIN can't go through MmapSource in WASM: the whole file would
// have to live in wasm linear memory (4 GiB ceiling) — and >2 GiB can't even
// be staged as one JS buffer. The range-streamed PagedByteSource is the only
// viable path: it fetches pages on demand and caps resident memory at the
// cache size. The fetcher here is a JS callback (node fs.readSync, which
// takes a 64-bit position) bridged into Python — the faithful analogue of an
// HTTP-Range / fetch fetcher in the browser. NB: FileRangeSource/os.pread is
// NOT usable for this in WASM — emscripten's off_t is 32-bit, so any offset
// past 2 GiB overflows; a fetch-style fetcher is the only option past 2 GiB.
//
// Gated on EIGEN_SIN (path to a large .sin); skips cleanly when unset, since
// the file is far too large to commit as a fixture.
//
//   EIGEN_SIN=/path/to/EigenR100.SIN \
//     node tools/pyodide-test/test_pyodide_sin_large.js

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DIST_DIR = path.join(REPO_ROOT, "dist_pyodide");

// Reference for EigenR100.SIN (from CPython read_sin_metadata + run_compare).
const EXPECT = {node_count: 5867, element_count: 7803, n_steps: 200, rvnoddis_step_rows: 5867};

function resolveAdapyWheel() {
    const explicit = process.env.ADAPY_WHEEL;
    if (explicit) return explicit;
    const wheels = fs.readdirSync(DIST_DIR).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${DIST_DIR}; build the wheel first`);
    return path.join(DIST_DIR, wheels[wheels.length - 1]);
}

(async () => {
    const sinPath = process.env.EIGEN_SIN;
    if (!sinPath) {
        console.log("SKIP — set EIGEN_SIN=/path/to/large.sin to run the streaming WASM test.");
        process.exit(0);
    }
    if (!fs.existsSync(sinPath)) throw new Error(`EIGEN_SIN=${sinPath} does not exist`);
    const sizeGb = fs.statSync(sinPath).size / 1e9;
    const dir = path.dirname(sinPath);
    const base = path.basename(sinPath);
    const wheelPath = resolveAdapyWheel();
    console.log(`file: ${sinPath} (${sizeGb.toFixed(2)} GB)\nadapy wheel: ${wheelPath}`);

    const py = await loadPyodide();
    py.FS.mkdirTree("/dist");
    py.FS.writeFile("/dist/" + path.basename(wheelPath), fs.readFileSync(wheelPath));
    await py.loadPackage(["micropip", "numpy"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install("emfs:/dist/" + path.basename(wheelPath), false);

    // Browser-fetch analogue: a JS range reader over the host file. node
    // fs.readSync takes a 64-bit position, so this works past 2 GiB where
    // os.pread can't. In the browser this is a ranged fetch() instead.
    const fd = fs.openSync(sinPath, "r");
    const fileSize = fs.statSync(sinPath).size;
    globalThis.hostRead = (offset, length) => {
        const buf = Buffer.allocUnsafe(length);
        const n = fs.readSync(fd, buf, 0, length, offset);
        return new Uint8Array(buf.buffer, buf.byteOffset, n);
    };
    py.globals.set("HOST_SIZE", fileSize);

    const out = py.runPython(`
import json, numpy as np
from js import hostRead
from ada.fem.formats.sesam.results.byte_source import PagedByteSource
from ada.fem.formats.sesam.results.sin_reader import SinFile
from ada.fem.formats.sesam.results.read_sin import SinReader, _RV_TYPE_NAMES

class JsRangeFetcher:
    """Range fetcher backed by a JS callback — the in-browser fetch path.
    Returns exactly the requested bytes (or fewer at EOF)."""
    def __init__(self, size):
        self._size = int(size)
    def size(self):
        return self._size
    def fetch(self, offset, length):
        if length <= 0:
            return b""
        return bytes(hostRead(offset, length).to_py())
    def close(self):
        pass

def run():
    try:
        # Metadata (cheap enumeration) over the streamed backend.
        src = PagedByteSource(JsRangeFetcher(HOST_SIZE), page_bits=20, max_resident_bytes=256 << 20)
        sin = SinFile(source=src)
        types = list(sin.type_blocks)
        node_count = sin.get_count("GCOORD")
        element_count = sin.get_count("GELMNT1")
        steps = set()
        for rv in _RV_TYPE_NAMES:
            if rv in sin.type_blocks:
                ires = sin.gather_first_words(rv)
                if ires.size:
                    steps.update(int(x) for x in np.unique(ires.astype(np.int64)).tolist())
        steps = sorted(steps)
        meta_fetched = src.bytes_fetched
        meta_peak = src.peak_resident_bytes

        # One mode (the streaming-bake shape): just step=1's RVNODDIS records.
        after = src.bytes_fetched
        arr = sin.gather_records("RVNODDIS", where_first_word=steps[0])
        step_fetched = src.bytes_fetched - after
        sin.close()

        return json.dumps({
            "ok": True,
            "blocks": len(types),
            "node_count": node_count,
            "element_count": element_count,
            "n_steps": len(steps),
            "rvnoddis_step_rows": int(arr.shape[0]),
            "rvnoddis_step_cols": int(arr.shape[1]),
            "discovery_mb": round(meta_fetched / 1e6, 1),
            "step_mb": round(step_fetched / 1e6, 1),
            "peak_resident_mb": round(meta_peak / 1e6, 1),
            "fetch_requests": src.fetch_count,
        })
    except Exception as e:
        import traceback
        return json.dumps({"ok": False, "err": type(e).__name__ + ": " + str(e), "tb": traceback.format_exc()})

run()
`);

    const r = JSON.parse(out);
    console.log(JSON.stringify(r, null, 2));
    if (!r.ok) {
        console.error("FAIL — streaming read raised:\n" + (r.tb || r.err));
        process.exit(1);
    }
    const fail = [];
    if (r.node_count !== EXPECT.node_count) fail.push(`node_count ${r.node_count} != ${EXPECT.node_count}`);
    if (r.element_count !== EXPECT.element_count) fail.push(`element_count ${r.element_count}`);
    if (r.n_steps !== EXPECT.n_steps) fail.push(`n_steps ${r.n_steps} != ${EXPECT.n_steps}`);
    if (r.rvnoddis_step_rows !== EXPECT.rvnoddis_step_rows) {
        fail.push(`rvnoddis step rows ${r.rvnoddis_step_rows} != ${EXPECT.rvnoddis_step_rows}`);
    }
    if (r.peak_resident_mb > 600) fail.push(`peak resident ${r.peak_resident_mb} MB unexpectedly high`);
    if (fail.length) {
        console.error("\nFAIL — large-SIN WASM streaming mismatches:");
        for (const f of fail) console.error("  - " + f);
        process.exit(1);
    }
    console.log(
        `\nOK — streamed a ${sizeGb.toFixed(1)} GB SIN under pyodide: ` +
        `${r.n_steps} steps, ${r.node_count} nodes; discovery ${r.discovery_mb} MB, ` +
        `one mode +${r.step_mb} MB, peak resident ${r.peak_resident_mb} MB ` +
        `(file never fully in wasm memory).`,
    );
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
