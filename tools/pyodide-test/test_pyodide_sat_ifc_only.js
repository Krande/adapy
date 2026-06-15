// Minimal: does SAT -> IFC work in a FRESH pyodide with NOTHING run before it?
// (Isolates whether the build_advanced_face_planar GeomAdaptor_Curve::BSpline
// failure is order/global-state dependent — i.e. polluted by prior IFC ops.)
const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");
const REPO = path.resolve(__dirname, "..", "..");
const IFC_WASM_WHEEL =
    "https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl";
function resolveWheel(envVar, dir) {
    const explicit = process.env[envVar];
    if (explicit) return explicit;
    const wheels = fs.readdirSync(dir).filter((f) => f.endsWith(".whl")).sort();
    return path.join(dir, wheels[wheels.length - 1]);
}
(async () => {
    const adacppWheel = resolveWheel("ADACPP_WHEEL", path.resolve(REPO, "..", "adacpp", "dist"));
    const adapyWheel = resolveWheel("ADAPY_WHEEL", path.join(REPO, "dist_pyodide"));
    const py = await loadPyodide();
    py.setStderr({write: (b) => { process.stderr.write(Buffer.from(b)); return b.length; }});
    py.FS.mkdirTree("/dist");
    for (const w of [adacppWheel, adapyWheel]) py.FS.writeFile("/dist/" + path.basename(w), fs.readFileSync(w));
    py.FS.writeFile("/fixture.sat", fs.readFileSync(path.join(REPO, "files", "sat_files", "flat_plate_sesam_10x10.sat")));
    await py.loadPackage(["micropip", "numpy", "h5py", "pydantic", "Pillow"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install(IFC_WASM_WHEEL);
    await mp.install("emfs:/dist/" + path.basename(adacppWheel), false);
    await mp.install("emfs:/dist/" + path.basename(adapyWheel), false);
    const result = py.runPython(`
import os, traceback
import ada.cad
ada.cad.select_backend(prefer="adacpp")
import ada
import io, ifcopenshell  # noqa: F401  (ifcopenshell's OWN OCCT now loaded)
out = {}
try:
    a = ada.from_acis("/fixture.sat")
    b = io.BytesIO(); a.to_gltf(b)
    out["sat_to_glb_with_ifcopenshell_loaded"] = {"ok": True, "bytes": len(b.getvalue())}
except Exception as e:
    out["sat_to_glb_with_ifcopenshell_loaded"] = {"ok": False, "err": type(e).__name__ + ": " + str(e)}
try:
    a2 = ada.from_acis("/fixture.sat")
    a2.to_ifc("/out.ifc")
    out["sat_to_ifc"] = {"ok": True, "bytes": os.path.getsize("/out.ifc")}
except Exception as e:
    out["sat_to_ifc"] = {"ok": False, "err": type(e).__name__ + ": " + str(e)}
out
`);
    console.log(JSON.stringify(result.toJs({dict_converter: Object.fromEntries})));
    result.destroy();
})().catch((e) => { console.error(String(e).split("\n").slice(0,6).join("\n")); process.exit(1); });
