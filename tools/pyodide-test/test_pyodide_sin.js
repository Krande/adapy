// End-to-end test: read a Sesam SIN binary result file under real pyodide.
//
// Proves the pure-python SIN reader (sin_reader.py + read_sin.py over the
// ByteSource abstraction) produces a correct FEAResult in WASM — no mmap
// (open_sin falls back to a bytes-backed MmapSource on emscripten MEMFS),
// no native CAD kernels, just numpy. This is the in-browser FEA-result path
// for .sin sources. Parity is asserted against the CPython reference values
// for the committed cantilever fixture.

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DIST_DIR = path.join(REPO_ROOT, "dist_pyodide");
const SIN_FIXTURE = path.join(
    REPO_ROOT,
    "files/fem_files/cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN",
);

// CPython reference (PYTHONPATH=src python -c ... read_sin_file(fixture)).
const EXPECT = {
    node_count: 403,
    element_count: 360,
    steps: [1],
    mesh_nodes: [403, 3],
    fields: {
        RVNODDIS: {shape: [403, 7], abs_sum: 81409.625},
        STRESS: {shape: [3600, 5], abs_sum: 239372907499.127},
    },
};

function resolveAdapyWheel() {
    const explicit = process.env.ADAPY_WHEEL;
    if (explicit) {
        if (!fs.existsSync(explicit)) throw new Error(`ADAPY_WHEEL=${explicit} does not exist`);
        return explicit;
    }
    if (!fs.existsSync(DIST_DIR)) {
        throw new Error(`No ${DIST_DIR}. Build the wheel: python tools/build_pyodide_adapy_wheel.py`);
    }
    const wheels = fs.readdirSync(DIST_DIR).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${DIST_DIR}`);
    return path.join(DIST_DIR, wheels[wheels.length - 1]);
}

function approx(a, b, rtol = 1e-6) {
    return Math.abs(a - b) <= rtol * Math.max(1, Math.abs(b));
}

(async () => {
    const wheelPath = resolveAdapyWheel();
    console.log(`adapy wheel: ${wheelPath}`);
    if (!fs.existsSync(SIN_FIXTURE)) throw new Error(`missing fixture ${SIN_FIXTURE}`);

    const py = await loadPyodide();

    py.FS.mkdirTree("/dist");
    py.FS.writeFile("/dist/" + path.basename(wheelPath), fs.readFileSync(wheelPath));
    // The SIN reader is numpy-only; trimesh+pyquaternion are needed merely
    // because `import ada` (run by the reader's parent package) touches them.
    await py.loadPackage(["micropip", "numpy"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install("emfs:/dist/" + path.basename(wheelPath), false /* deps */);

    py.FS.mkdirTree("/work");
    py.FS.writeFile("/work/cantilever.SIN", fs.readFileSync(SIN_FIXTURE));

    const result = py.runPython(`
import json, sys
import numpy as np
from ada.fem.formats.sesam.results.read_sin import read_sin_file, read_sin_metadata
from ada.fem.formats.sesam.results.sin_reader import open_sin

f = "/work/cantilever.SIN"
# Which backend did open_sin pick on emscripten? (mmap or bytes fallback)
_sf = open_sin(f)
backend = type(_sf.source).__name__
buf = type(getattr(_sf.source, "_buf", None)).__name__
_sf.close()

meta = read_sin_metadata(f)
res = read_sin_file(f)
fields = {}
for x in res.results:
    v = np.asarray(x.values, dtype=float)
    fields[x.name] = {"shape": list(v.shape), "abs_sum": float(np.nansum(np.abs(v)))}

json.dumps({
    "platform": sys.platform,
    "backend": backend,
    "buf_type": buf,
    "name": res.name,
    "node_count": meta.node_count,
    "element_count": meta.element_count,
    "steps": list(meta.steps),
    "mesh_nodes": list(res.mesh.nodes.coords.shape),
    "n_results": len(res.results),
    "fields": fields,
})
`);

    const r = JSON.parse(result);
    console.log(JSON.stringify(r, null, 2));

    const fail = [];
    if (r.platform !== "emscripten") fail.push(`platform ${r.platform} != emscripten`);
    if (r.node_count !== EXPECT.node_count) fail.push(`node_count ${r.node_count} != ${EXPECT.node_count}`);
    if (r.element_count !== EXPECT.element_count) fail.push(`element_count ${r.element_count}`);
    if (JSON.stringify(r.steps) !== JSON.stringify(EXPECT.steps)) fail.push(`steps ${r.steps}`);
    if (JSON.stringify(r.mesh_nodes) !== JSON.stringify(EXPECT.mesh_nodes)) fail.push(`mesh_nodes ${r.mesh_nodes}`);
    for (const [name, exp] of Object.entries(EXPECT.fields)) {
        const got = r.fields[name];
        if (!got) {
            fail.push(`missing field ${name}`);
            continue;
        }
        if (JSON.stringify(got.shape) !== JSON.stringify(exp.shape)) fail.push(`${name} shape ${got.shape}`);
        if (!approx(got.abs_sum, exp.abs_sum)) fail.push(`${name} abs_sum ${got.abs_sum} != ${exp.abs_sum}`);
    }

    if (fail.length) {
        console.error("\nFAIL — SIN WASM read mismatches:");
        for (const f of fail) console.error("  - " + f);
        process.exit(1);
    }
    console.log(
        `\nOK — read SIN under pyodide (${r.backend}/${r.buf_type}); ` +
        `FEAResult matches CPython byte-for-byte.`,
    );
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
