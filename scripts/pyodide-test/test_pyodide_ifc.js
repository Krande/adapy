// Does IFC round-trip under pyodide? The other harnesses SKIPPED IFC because
// the ifcopenshell wasm wheel targets the pyodide_2025_0 ABI, which only
// matched once we moved to pyodide 0.29.4. Now it should load in node-pyodide.
//
// Exercises both directions through adapy:
//   - read:  ada.from_ifc(fixture) -> to_gltf            (IFC -> GLB)
//   - write: from_step / from_acis -> to_ifc             (anything -> IFC)
//   - rt:    from_ifc -> to_ifc                           (IFC -> IFC)

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

const IFC_FIXTURE = path.join(REPO, "files", "ifc_files", "box_rotated.ifc");
const SAT_FIXTURE = path.join(REPO, "files", "sat_files", "flat_plate_sesam_10x10.sat");

(async () => {
    const adacppWheel = resolveWheel("ADACPP_WHEEL", path.resolve(REPO, "..", "adacpp", "dist"), "adacpp");
    const adapyWheel = resolveWheel("ADAPY_WHEEL", path.join(REPO, "dist_pyodide"), "adapy");
    const py = await loadPyodide();
    py.setStderr({write: (buf) => { process.stderr.write(Buffer.from(buf)); return buf.length; }});
    py.FS.mkdirTree("/dist");
    for (const w of [adacppWheel, adapyWheel]) py.FS.writeFile("/dist/" + path.basename(w), fs.readFileSync(w));
    py.FS.writeFile("/box.ifc", fs.readFileSync(IFC_FIXTURE));
    py.FS.writeFile("/fixture.sat", fs.readFileSync(SAT_FIXTURE));

    await py.loadPackage(["micropip", "numpy", "h5py", "pydantic", "Pillow"]);
    const mp = py.pyimport("micropip");
    await mp.install(["trimesh", "pyquaternion"]);
    await mp.install(IFC_WASM_WHEEL);
    await mp.install("emfs:/dist/" + path.basename(adacppWheel), false);
    await mp.install("emfs:/dist/" + path.basename(adapyWheel), false);

    const result = py.runPython(`
import io, os, traceback
import ada.cad
ada.cad.select_backend(prefer="adacpp")
import ada
import ifcopenshell

out = {"ifcopenshell_version": ifcopenshell.version}

def attempt(name, fn):
    try:
        out[name] = {"ok": True, **fn()}
    except Exception as e:
        out[name] = {"ok": False, "err": type(e).__name__ + ": " + str(e),
                     "tb": traceback.format_exc()[-400:]}

# 1) IFC -> GLB (read path)
def ifc_to_glb():
    a = ada.from_ifc("/box.ifc")
    b = io.BytesIO(); a.to_gltf(b)
    return {"glb_bytes": len(b.getvalue())}
attempt("ifc_to_glb", ifc_to_glb)

# 2) IFC -> IFC (read+write round-trip)
def ifc_roundtrip():
    a = ada.from_ifc("/box.ifc")
    a.to_ifc("/out_rt.ifc")
    return {"ifc_bytes": os.path.getsize("/out_rt.ifc")}
attempt("ifc_roundtrip", ifc_roundtrip)

# 3) SAT -> IFC (write path from a non-IFC source)
def sat_to_ifc():
    a = ada.from_acis("/fixture.sat")
    a.to_ifc("/out_sat.ifc")
    return {"ifc_bytes": os.path.getsize("/out_sat.ifc")}
attempt("sat_to_ifc", sat_to_ifc)

# 4) IFC -> {obj, stl, step, xml} — the rest of the ifc row of the wasm matrix.
def ifc_to(target):
    def _fn():
        a = ada.from_ifc("/box.ifc")
        if target in ("obj", "stl"):
            data = a.to_trimesh_scene().export(file_type=target)
            n = len(data.encode() if isinstance(data, str) else data)
        elif target == "step":
            a.to_stp("/out.step"); n = os.path.getsize("/out.step")
        elif target == "xml":
            a.to_genie_xml("/out.xml"); n = os.path.getsize("/out.xml")
        return {"bytes": n}
    return _fn
for _t in ("obj", "stl", "step", "xml"):
    attempt(f"ifc_to_{_t}", ifc_to(_t))

out
`);
    const r = result.toJs({dict_converter: Object.fromEntries});
    result.destroy();
    console.log(JSON.stringify(r, null, 2));
})().catch((e) => {
    console.error(String(e).split("\n").slice(0, 8).join("\n"));
    process.exit(1);
});
