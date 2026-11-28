// Every GLB-parity fixture through the browser's entries under real pyodide, installing the ada-cpp
// wasm wheel and the adapy pyodide wheel as the browser worker does:
// `ada.cadit.wasm_convert.run` (the call the worker makes for every conversion cell) for the files,
// `ada.topo_model.wasm_compile.compile_doc` (the in-browser procedural compile) for the document.
//
// This script only PRODUCES the wasm GLBs, as <out_dir>/<fixture>.glb;
// tests/core/visit/test_glb_export_parity.py (test_pyodide_glb_matches_to_gltf, with
// ADA_WASM_PARITY_DIR=<out_dir>) compares each, element by element, with what `to_gltf` writes on
// the native ada-cpp. A fixture the browser stack cannot load here (the IFC ones when no
// ifcopenshell wheel is given) is written as <fixture>.skipped with the reason; any other failure
// fails the script.
//
//   node test_pyodide_glb_parity.js <out_dir>
//
// Wheels: $ADACPP_WHEEL and $ADAPY_WHEEL as in test_pyodide_genie_curved_shell.js; optional
// $IFCOPENSHELL_WHEEL (a pyodide ifcopenshell wheel) for the IFC fixtures.

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO = path.resolve(__dirname, "..", "..", "..");
const FILES = path.join(REPO, "files");
// Keep in step with FIXTURES in tests/core/visit/test_glb_export_parity.py.
const FIXTURES = {
    "genie-curved-shell": ["genie", "xml", "fem_files/sesam/curved_shell_nurbs_strake.xml"],
    "ifc-extruded-beam": ["ifc", "ifc", "ifc_files/beam-extruded-solid.ifc"],
    "ifc-csg-solid": ["ifc", "ifc", "ifc_files/bath-csg-solid.ifc"],
    "ifc-polygonal-mesh": ["ifc", "ifc", "ifc_files/bs_samples/polygonal-face-tessellation.ifc"],
    "step-curved-plate": ["step", "stp", "step_files/plate_3_curved.stp"],
    "fem-deck": ["fem", "FEM", "fem_files/sesam/beamMassT1.FEM"],
    "procedural": ["procedural", "json", "topo_model/two_cells_one_pump.json"],
};

function resolveWheel(envVar, dir, label, optional = false) {
    const explicit = process.env[envVar];
    if (explicit) {
        if (!fs.existsSync(explicit)) throw new Error(`${envVar}=${explicit} does not exist`);
        return explicit;
    }
    if (optional) return null;
    if (!fs.existsSync(dir)) throw new Error(`No ${dir} for ${label}; build the wheel first or set ${envVar}`);
    const wheels = fs.readdirSync(dir).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${dir} for ${label}`);
    return path.join(dir, wheels[wheels.length - 1]);
}

(async () => {
    const outDir = process.argv[2];
    if (!outDir) throw new Error("usage: node test_pyodide_glb_parity.js <out_dir>");
    const adacppWheel = resolveWheel("ADACPP_WHEEL", path.resolve(REPO, "..", "adacpp", "dist"), "adacpp");
    const adapyWheel = resolveWheel("ADAPY_WHEEL", path.join(REPO, "dist_pyodide"), "adapy");
    const ifcWheel = resolveWheel("IFCOPENSHELL_WHEEL", null, "ifcopenshell", true);
    console.log(`adacpp wheel: ${adacppWheel}`);
    console.log(`adapy  wheel: ${adapyWheel}`);
    console.log(`ifcopenshell wheel: ${ifcWheel || "(none: IFC fixtures are skipped)"}`);

    const py = await loadPyodide();
    py.FS.mkdirTree("/dist");
    py.FS.mkdirTree("/in");
    const wheels = [adacppWheel, adapyWheel].concat(ifcWheel ? [ifcWheel] : []);
    for (const w of wheels) py.FS.writeFile("/dist/" + path.basename(w), fs.readFileSync(w));
    for (const [name, [, ext, rel]] of Object.entries(FIXTURES)) {
        py.FS.writeFile(`/in/${name}.${ext}`, fs.readFileSync(path.join(FILES, rel)));
    }

    // The worker's core stack (pyodide_worker.js): numpy + Pillow + pydantic + sqlite3 built-ins
    // (+ h5py, which it loads for FEM decks), trimesh + pyquaternion from PyPI, then the wheels
    // without their (undeclared) deps.
    await py.loadPackage(["micropip", "numpy", "Pillow", "pydantic", "sqlite3", "h5py"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    for (const w of wheels) await mp.install("emfs:/dist/" + path.basename(w), false);

    fs.mkdirSync(path.resolve(outDir), {recursive: true});
    let failed = 0;
    for (const [name, [fmt, ext]] of Object.entries(FIXTURES)) {
        if (fmt === "ifc" && !ifcWheel) {
            fs.writeFileSync(path.join(outDir, `${name}.skipped`), "no ifcopenshell pyodide wheel given (IFCOPENSHELL_WHEEL)");
            console.log(`SKIP ${name}: no ifcopenshell wheel`);
            continue;
        }
        try {
            py.globals.set("_fmt", fmt);
            py.globals.set("_ext", ext);
            py.globals.set("_src", `/in/${name}.${ext}`);
            const res = py.runPython(`
import sys
assert sys.platform == "emscripten", sys.platform
if _fmt == "procedural":
    from ada.topo_model.wasm_compile import compile_doc
    with open(_src) as fh:
        _out = compile_doc(fh.read())
else:
    import ada.cadit.wasm_convert as wc
    _out = wc.run(_fmt, _ext, "glb", _src)
_out
`);
            const glb = Buffer.from(res.toJs());
            res.destroy();
            if (glb.subarray(0, 4).toString("latin1") !== "glTF") throw new Error("not a GLB");
            fs.writeFileSync(path.join(outDir, `${name}.glb`), glb);
            console.log(`OK ${name}: ${glb.length} bytes`);
        } catch (e) {
            failed += 1;
            console.error(`FAIL ${name}: ${e}`);
        }
    }
    if (failed) throw new Error(`${failed} fixture(s) failed to convert under pyodide`);
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
