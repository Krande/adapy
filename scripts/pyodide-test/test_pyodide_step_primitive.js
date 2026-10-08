// Isolation test: does adacpp's write_step crash for a TRIVIAL primitive
// (a backend-built box) directly, or only for SAT-derived geometry / the
// full to_stp path? Calls backend.write_step([box], ...) with no adapy
// Assembly, no SAT, no XCAF-from-import — the minimal STEP-write path.

const fs = require("fs");
const path = require("path");
const {loadPyodide} = require("pyodide");

const REPO = path.resolve(__dirname, "..", "..");

function resolveWheel(envVar, dir) {
    const explicit = process.env[envVar];
    if (explicit) return explicit;
    const wheels = fs.readdirSync(dir).filter((f) => f.endsWith(".whl")).sort();
    return path.join(dir, wheels[wheels.length - 1]);
}

(async () => {
    const adacppWheel = resolveWheel("ADACPP_WHEEL", path.resolve(REPO, "..", "adacpp", "dist"));
    const adapyWheel = resolveWheel("ADAPY_WHEEL", path.join(REPO, "dist_pyodide"));
    console.log("adacpp:", adacppWheel);
    const py = await loadPyodide();
    // Synchronous IO so C++ checkpoints flush BEFORE a hard abort() kills node
    // (pyodide's default batched stdout/stderr only flushes on a later tick).
    py.setStderr({write: (buf) => { process.stderr.write(Buffer.from(buf)); return buf.length; }});
    py.setStdout({write: (buf) => { process.stdout.write(Buffer.from(buf)); return buf.length; }});
    py.FS.mkdirTree("/dist");
    for (const w of [adacppWheel, adapyWheel]) py.FS.writeFile("/dist/" + path.basename(w), fs.readFileSync(w));
    await py.loadPackage(["micropip", "numpy"]);
    const mp = py.pyimport("micropip");
    await mp.install("emfs:/dist/" + path.basename(adacppWheel), false);
    await mp.install("emfs:/dist/" + path.basename(adapyWheel), false);

    const result = py.runPython(`
import os, traceback
import ada.cad
backend = ada.cad.select_backend(prefer="adacpp")
out = {}

# 1) box -> GLB (sanity: backend works)
box = backend.make_box(2.0, 3.0, 4.0)
out["box_glb_bytes"] = len(backend.write_glb_bytes(box))

# 2) box -> STEP via backend.write_step directly (the minimal write path)
try:
    backend.write_step([box], ["box"], [(1.0, 0.0, 0.0)], "/tmp/box.stp", "M", "AP214")
    out["box_step_bytes"] = os.path.getsize("/tmp/box.stp")
except Exception as e:
    out["box_step_error"] = type(e).__name__ + ": " + str(e)

# 3) round-trip: read a STEP we just... use the box step if it worked
out
`);
    const r = result.toJs({dict_converter: Object.fromEntries});
    result.destroy();
    console.log(JSON.stringify(r, null, 2));
})().catch((e) => {
    console.error(String(e).split("\\n").slice(0, 6).join("\\n"));
    process.exit(1);
});
