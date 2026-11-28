// Genie XML -> GLB through the browser's conversion entry under real pyodide, installing the
// ada-cpp wasm wheel and the adapy pyodide wheel exactly as the browser worker does, and
// `ada.cadit.wasm_convert.run` -- the call the worker makes for every cell.
//
// The fixture is one doubly-curved <curved_shell> (files/fem_files/sesam/curved_shell_nurbs_strake.xml).
// This script only PRODUCES the wasm GLB; tests/core/cadit/test_genie_curved_shell_glb.py
// (test_wasm_glb_matches_the_worker) compares it, plate by plate, with the GLB the worker's
// converter writes from the same file on the native ada-cpp.
//
//   node test_pyodide_genie_curved_shell.js <out.glb>
//
// Wheels: $ADACPP_WHEEL (the ada-cpp pyodide wheel, e.g. out of the adacpp-wasm-base image the
// viewer is built from) and $ADAPY_WHEEL (scripts/packaging/build_pyodide_adapy_wheel.py), or the
// newest .whl in ../adacpp/dist and ./dist_pyodide.

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO = path.resolve(__dirname, "..", "..", "..");
const FIXTURE = path.join(REPO, "files", "fem_files", "sesam", "curved_shell_nurbs_strake.xml");

function resolveWheel(envVar, dir, label) {
    const explicit = process.env[envVar];
    if (explicit) {
        if (!fs.existsSync(explicit)) throw new Error(`${envVar}=${explicit} does not exist`);
        return explicit;
    }
    if (!fs.existsSync(dir)) throw new Error(`No ${dir} for ${label}; build the wheel first or set ${envVar}`);
    const wheels = fs.readdirSync(dir).filter((f) => f.endsWith(".whl")).sort();
    if (!wheels.length) throw new Error(`No .whl in ${dir} for ${label}`);
    return path.join(dir, wheels[wheels.length - 1]);
}

(async () => {
    const out = process.argv[2];
    if (!out) throw new Error("usage: node test_pyodide_genie_curved_shell.js <out.glb>");
    const adacppWheel = resolveWheel("ADACPP_WHEEL", path.resolve(REPO, "..", "adacpp", "dist"), "adacpp");
    const adapyWheel = resolveWheel("ADAPY_WHEEL", path.join(REPO, "dist_pyodide"), "adapy");
    console.log(`adacpp wheel: ${adacppWheel}`);
    console.log(`adapy  wheel: ${adapyWheel}`);

    const py = await loadPyodide();
    py.FS.mkdirTree("/dist");
    for (const w of [adacppWheel, adapyWheel]) py.FS.writeFile("/dist/" + path.basename(w), fs.readFileSync(w));
    py.FS.writeFile("/input.xml", fs.readFileSync(FIXTURE));

    // The worker's core stack (pyodide_worker.js): numpy + Pillow + pydantic + sqlite3 built-ins,
    // trimesh + pyquaternion from PyPI, then the two wheels without their (undeclared) deps.
    await py.loadPackage(["micropip", "numpy", "Pillow", "pydantic", "sqlite3"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install("emfs:/dist/" + path.basename(adacppWheel), false);
    await mp.install("emfs:/dist/" + path.basename(adapyWheel), false);

    const res = py.runPython(`
import sys
import ada.cadit.wasm_convert as wc
assert sys.platform == "emscripten", sys.platform
wc.run("genie", "xml", "glb", "/input.xml")
`);
    const glb = Buffer.from(res.toJs());
    res.destroy();
    if (glb.subarray(0, 4).toString("latin1") !== "glTF") throw new Error("the wasm conversion did not produce a GLB");
    fs.mkdirSync(path.dirname(path.resolve(out)), {recursive: true});
    fs.writeFileSync(out, glb);
    console.log(`OK: wrote ${glb.length} bytes to ${out}`);
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
