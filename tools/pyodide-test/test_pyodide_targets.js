// Probe which OUTPUT formats adapy can write under pyodide, from a single
// in-browser-loaded Assembly. The audit matrix expands each geometry
// source to {glb, ifc, obj, step, stl, xml}; this tells us which of those
// the WASM engine can actually produce (vs which need the OCC/gmsh worker).
//
// Loads the SAT fixture via ada.from_acis (pure-python parser → ada.geom,
// known to work in pyodide) and tries every exporter. Reports per-target
// ok / error so the worker's generic converter can wire the supported set
// and route the rest to the server.

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO = path.resolve(__dirname, "..", "..");
const IFC_WASM_WHEEL =
    "https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl";

function resolveWheel(envVar, dir, label) {
    const explicit = process.env[envVar];
    if (explicit) return explicit;
    const wheels = fs.readdirSync(dir).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${dir} for ${label}`);
    return path.join(dir, wheels[wheels.length - 1]);
}

const SAT_FIXTURE = path.join(REPO, "files", "sat_files", "flat_plate_sesam_10x10.sat");

(async () => {
    const adacppWheel = resolveWheel("ADACPP_WHEEL", path.resolve(REPO, "..", "adacpp", "dist"), "adacpp");
    const adapyWheel = resolveWheel("ADAPY_WHEEL", path.join(REPO, "dist_pyodide"), "adapy");
    const py = await loadPyodide();
    py.FS.mkdirTree("/dist");
    for (const w of [adacppWheel, adapyWheel]) py.FS.writeFile("/dist/" + path.basename(w), fs.readFileSync(w));
    py.FS.writeFile("/fixture.sat", fs.readFileSync(SAT_FIXTURE));

    await py.loadPackage(["micropip", "numpy", "h5py", "pydantic", "Pillow"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    // NOTE: node-pyodide here now carries the pyodide_2025_0 platform tag
    // (0.29.4 / emscripten 4.0.9), so ifcopenshell's wasm wheel is ABI-
    // compatible — but to_ifc is exercised in the IFC-specific browser
    // harness, so we keep this probe scoped to the non-IFC targets.
    void IFC_WASM_WHEEL;
    await mp.install("emfs:/dist/" + path.basename(adacppWheel), false);
    await mp.install("emfs:/dist/" + path.basename(adapyWheel), false);

    const result = py.runPython(`
import io, os, traceback
import ada.cad
ada.cad.select_backend(prefer="adacpp")
import ada

asm = ada.from_acis("/fixture.sat")
out = {}

def attempt(name, fn):
    try:
        n = fn()
        out[name] = {"ok": True, "bytes": n}
    except Exception as e:
        out[name] = {"ok": False, "err": type(e).__name__ + ": " + str(e)}

def glb():
    b = io.BytesIO(); asm.to_gltf(b); return len(b.getvalue())
def obj():
    return len(asm.to_trimesh_scene().export(file_type="obj"))
def stl():
    return len(asm.to_trimesh_scene().export(file_type="stl"))
def xml():
    asm.to_genie_xml("/tmp/out.xml"); return os.path.getsize("/tmp/out.xml")
attempt("glb", glb)
attempt("obj", obj)
attempt("stl", stl)
attempt("xml", xml)
def stp():
    asm.to_stp("/tmp/out.stp"); return os.path.getsize("/tmp/out.stp")
attempt("step", stp)
# to_stp (STEPCAFControl_Writer via the adacpp backend) works under wasm as of
# the 0.29.4/wasm-EH toolchain: OCCT is built with -fwasm-exceptions and the
# BinXCAF document driver, and step_writer.cpp/helpers.cpp are now compiled into
# the side module (previously omitted, so write_shapes_to_step was an unresolved
# env import that trapped with unreachable on first call).
out
`);
    const r = result.toJs({dict_converter: Object.fromEntries});
    result.destroy();
    console.log(JSON.stringify(r, null, 2));
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
