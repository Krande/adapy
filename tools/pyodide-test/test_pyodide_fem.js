// Import-safety smoke test for the adapy FEM + SAT pyodide path.
//
// Unlike test_pyodide_cad.js this needs NO adacpp wheel — it only proves
// that the FEM-result-baking and SAT-reader import chains load under real
// pyodide (0.27.7) with just numpy + h5py (pyodide built-ins) + trimesh
// (micropip). If any module on these chains eagerly imports a native-only
// dep (pythonocc-core / gmsh / ifcopenshell), the import fails here — which
// is exactly what we want to catch before wiring the browser FEM stack.
//
// h5py + libhdf5 ship in the pyodide lockfile (added in 0.26), so .rmed/.med
// reading is viable in-browser; this test confirms adapy's readers import
// without dragging in the heavy CAD kernels.

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DIST_DIR = path.join(REPO_ROOT, "dist_pyodide");

function resolveAdapyWheel() {
    const explicit = process.env.ADAPY_WHEEL;
    if (explicit) {
        if (!fs.existsSync(explicit)) throw new Error(`ADAPY_WHEEL=${explicit} does not exist`);
        return explicit;
    }
    if (!fs.existsSync(DIST_DIR)) {
        throw new Error(
            `No ${DIST_DIR}. Build the wheel first: ` +
            `python tools/build_pyodide_adapy_wheel.py`,
        );
    }
    const wheels = fs.readdirSync(DIST_DIR).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${DIST_DIR}`);
    return path.join(DIST_DIR, wheels[wheels.length - 1]);
}

(async () => {
    const wheelPath = resolveAdapyWheel();
    console.log(`adapy wheel: ${wheelPath}`);
    const py = await loadPyodide();

    // Install the actual built wheel (the real deliverable) — not a source
    // mount — so this validates the packaged artifact. deps=False mirrors
    // the browser worker: the wheel's metadata declares no deps; we supply
    // the wasm-available ones ourselves.
    py.FS.mkdirTree("/dist");
    py.FS.writeFile("/dist/" + path.basename(wheelPath), fs.readFileSync(wheelPath));

    // numpy + h5py are pyodide built-ins; trimesh + pyquaternion are
    // pure-python wheels the FEM/SAT chains pull in.
    await py.loadPackage(["micropip", "numpy", "h5py"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install("emfs:/dist/" + path.basename(wheelPath), false /* deps */);

    const result = py.runPython(`
import sys, importlib, traceback

def try_import(name):
    try:
        importlib.import_module(name)
        return "ok"
    except Exception as e:
        return type(e).__name__ + ": " + str(e)

results = {}
for mod in [
    "ada",
    "ada.fem",
    "ada.fem.results",
    "ada.fem.results.artefacts",
    "ada.fem.results.common",
    "ada.fem.formats.code_aster.results.read_rmed_results",
    "ada.fem.formats.sesam.results.read_sif",
    "ada.cadit.sat",
    "ada.cadit.sat.store",
    "ada.cadit.sat.parser",
    "ada.occ.tessellating",
    "ada.api.beams",
    "ada.api.plates",
    "ada.api.spatial",
    "ada.api.primitives",
    "ada.visit.gltf.glb",
]:
    results[mod] = try_import(mod)

# The concrete entrypoints the browser FEM/SAT stacks call.
def try_attr(stmt):
    try:
        exec(stmt, {})
        return "ok"
    except Exception as e:
        return type(e).__name__ + ": " + str(e)

entrypoints = {
    "bake_fea_artefacts_from_source":
        try_attr("from ada.fem.results.artefacts import bake_fea_artefacts_from_source"),
}

{"platform": sys.platform, "results": results, "entrypoints": entrypoints}
`);

    const r = result.toJs({dict_converter: Object.fromEntries});
    result.destroy();
    console.log(JSON.stringify(r, null, 2));

    if (r.platform !== "emscripten") {
        console.error(`FAIL: expected emscripten platform, got ${r.platform}`);
        process.exit(1);
    }
    const failures = [];
    for (const [k, v] of Object.entries(r.results)) {
        if (v !== "ok") failures.push(`import ${k}: ${v}`);
    }
    for (const [k, v] of Object.entries(r.entrypoints)) {
        if (v !== "ok") failures.push(`entrypoint ${k}: ${v}`);
    }
    if (failures.length) {
        console.error("\nFAIL: pyodide import-safety regressions:");
        for (const f of failures) console.error("  - " + f);
        process.exit(1);
    }
    console.log("\nOK — adapy FEM + SAT import chains are pyodide-safe (numpy+h5py+trimesh only).");
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
