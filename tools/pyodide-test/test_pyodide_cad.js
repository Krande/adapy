// Local smoke test for the adapy ↔ adacpp pyodide path.
//
// The harness exercises:
//   1. The adacpp wasm wheel actually loads under pyodide
//   2. adapy's pyodide entrypoint (deploy/pyodide-ada-init.py, swapped in
//      over ada/__init__.py the same way the Docker build does) lets
//      `import ada.cad` succeed without the heavy native deps
//      (pythonocc-core, gmsh, ...)
//   3. AdacppBackend.tessellate(make_box/cylinder/sphere(...)) all produce
//      real OCCT meshes through the abstraction layer (wasm now links
//      OCCT statically — no more stub kernel)
//   4. bbox produces exact analytic extents for primitives
//   5. from_topods_pointer is exposed on wasm and rejects a null pointer
//
// Resolves the adacpp wheel from $ADACPP_WHEEL or globs the adacpp
// dist directory that lives next to this repo. No coupling between
// the two pixi solves; each repo builds its own wheel.

const fs   = require("fs");
const path = require("path");
const { loadPyodide } = require("pyodide");

const REPO_ROOT      = path.resolve(__dirname, "..", "..");
const ADAPY_SRC      = path.join(REPO_ROOT, "src");
const ADAPY_PKG      = path.join(ADAPY_SRC, "ada");
const PYODIDE_INIT   = path.join(REPO_ROOT, "deploy", "pyodide-ada-init.py");
const DEFAULT_DIST   = path.resolve(REPO_ROOT, "..", "adacpp", "dist");

function resolveWheel() {
    const explicit = process.env.ADACPP_WHEEL;
    if (explicit) {
        if (!fs.existsSync(explicit)) {
            throw new Error(`ADACPP_WHEEL=${explicit} does not exist`);
        }
        return explicit;
    }
    if (!fs.existsSync(DEFAULT_DIST)) {
        throw new Error(
            `No adacpp dist dir at ${DEFAULT_DIST}. Either build the wheel ` +
            `(cd ../adacpp && pixi run pack-wheel-pyodide) or set ADACPP_WHEEL.`
        );
    }
    const wheels = fs.readdirSync(DEFAULT_DIST).filter((f) => f.endsWith(".whl"));
    if (wheels.length === 0) {
        throw new Error(`No .whl files in ${DEFAULT_DIST}`);
    }
    wheels.sort();
    return path.join(DEFAULT_DIST, wheels[wheels.length - 1]);
}

function approxEq(a, b, tol = 1e-6) {
    return Math.abs(a - b) <= tol;
}

// Walk a directory and copy every regular file into pyodide's FS at
// `dest/<rel-path>`. Skips hidden dirs and __pycache__ to keep the FS
// uncluttered. Used to mount adapy/src/ada as a real Python package.
function copyDirToPyodideFS(py, srcDir, destDir, opts = {}) {
    const exts = opts.exts || [".py", ".pyi", ".typed"];
    py.FS.mkdirTree(destDir);
    for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
        if (entry.name.startsWith(".") || entry.name === "__pycache__") continue;
        const srcPath = path.join(srcDir, entry.name);
        const destPath = destDir + "/" + entry.name;
        if (entry.isDirectory()) {
            copyDirToPyodideFS(py, srcPath, destPath, opts);
        } else if (entry.isFile() && exts.some((e) => entry.name.endsWith(e))) {
            py.FS.writeFile(destPath, fs.readFileSync(srcPath));
        }
    }
}

(async () => {
    const wheelPath = resolveWheel();
    console.log(`adacpp wheel: ${wheelPath}`);
    if (!fs.existsSync(ADAPY_PKG)) {
        throw new Error(`Expected adapy package at ${ADAPY_PKG} but it's missing`);
    }

    const py = await loadPyodide();

    // Mount the wheel under emfs:/dist for micropip.
    py.FS.mkdirTree("/dist");
    py.FS.writeFile(
        "/dist/" + path.basename(wheelPath),
        fs.readFileSync(wheelPath),
    );

    // Mount the adacpp STEP fixture so we can round-trip STEP→GLB through
    // the abstraction layer under pyodide. Stored at the root of the FS
    // because adacpp lives next to adapy on disk; we resolve through the
    // wheel's dist dir.
    const stepFixture = path.resolve(
        path.dirname(wheelPath), "..", "files",
        "flat_plate_abaqus_10x10_m_wColors.stp",
    );
    if (fs.existsSync(stepFixture)) {
        py.FS.writeFile("/fixture.stp", fs.readFileSync(stepFixture));
    } else {
        console.warn(`STEP fixture not found at ${stepFixture}; STEP/GLB round-trip will be skipped`);
    }

    // Mount adapy/src on pyodide FS so `import ada.cad` resolves through
    // the real package. The real ada/__init__.py imports adapy's full
    // native-dep surface and can't run under wasm, so overlay the pyodide
    // entrypoint in its place — exactly what deploy/Dockerfile.viewer does
    // when staging adapy for the browser worker.
    py.FS.mkdirTree("/adapy_src");
    copyDirToPyodideFS(py, ADAPY_PKG, "/adapy_src/ada");
    if (!fs.existsSync(PYODIDE_INIT)) {
        throw new Error(`Expected pyodide entrypoint at ${PYODIDE_INIT} but it's missing`);
    }
    py.FS.writeFile("/adapy_src/ada/__init__.py", fs.readFileSync(PYODIDE_INIT));

    await py.loadPackage(["micropip"]);
    const mp = py.pyimport("micropip");
    await mp.install("emfs:/dist/" + path.basename(wheelPath));

    const result = py.runPython(`
import sys
sys.path.insert(0, "/adapy_src")

# This import path goes through the pyodide ada/__init__.py — it must
# skip the heavy imports (pythonocc-core, gmsh, ...) so this load
# doesn't raise.
import ada
import ada.cad

# Sanity: native-only top-level names must NOT be present in pyodide.
heavy_names = ["Assembly", "Part", "Beam", "Plate", "from_step"]
leaked = [n for n in heavy_names if hasattr(ada, n)]

backend = ada.cad.select_backend(prefer="adacpp")
assert backend.name == "adacpp", f"expected adacpp, got {backend.name!r}"

box = backend.make_box(2.0, 3.0, 4.0)
mesh = backend.tessellate(box)
positions = list(mesh.positions)
xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
box_aabb = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
n_box_tris = len(mesh.indices) // 3

# bbox: queries through the abstraction must produce the analytic result
# regardless of which kernel underlies the backend.
bb_box = backend.bbox(box)

def expect_raises(thunk):
    try:
        thunk()
        return None
    except Exception as e:
        return type(e).__name__ + ": " + str(e)

cyl = backend.make_cylinder(1.0, 5.0)
sph = backend.make_sphere(2.0)
cyl_mesh = backend.tessellate(cyl)
sph_mesh = backend.tessellate(sph)
n_cyl_tris = len(cyl_mesh.indices) // 3
n_sph_tris = len(sph_mesh.indices) // 3

# STEP read + GLB write through the CadBackend abstraction. Both ops live
# entirely in wasm/pyodide — no native dep needed for this conversion path,
# which is the point of the adacpp ↔ adapy migration.
import os
if os.path.exists("/fixture.stp"):
    with open("/fixture.stp", "rb") as f:
        step_bytes = f.read()
    step_handle = backend.read_step_bytes(step_bytes)
    step_glb = backend.write_glb_bytes(step_handle)
    step_round_trip = {
        "input_bytes": len(step_bytes),
        "output_bytes": len(step_glb),
        "magic": step_glb[:4].decode("latin-1"),
        "version": int.from_bytes(step_glb[4:8], "little"),
    }
else:
    step_round_trip = None

# Also check write_glb_bytes from a synthesized primitive — exercises the
# adacpp.cad path without depending on the fixture being present.
prim_glb = backend.write_glb_bytes(backend.make_box(2.0, 3.0, 4.0))
prim_round_trip = {
    "output_bytes": len(prim_glb),
    "magic": prim_glb[:4].decode("latin-1"),
    "version": int.from_bytes(prim_glb[4:8], "little"),
}

# from_topods_pointer is exposed on wasm too (same OCCT kernel both targets);
# null pointer raises RuntimeError, valid OCCT pointers can't exist under
# pyodide because pythonocc-core doesn't run there.
ptr_err = expect_raises(lambda: backend.from_topods_pointer(0))

# bbox: analytic extents (AddOptimal w/ useTriangulation=False) — exact for
# primitives, regardless of whether they've been tessellated already.
bb_cyl = backend.bbox(cyl)
bb_sph = backend.bbox(sph)

# Confirm OccBackend is unavailable here (no pythonocc-core wheel for wasm).
occ_err = expect_raises(lambda: ada.cad.OccBackend())

{
  "platform":           sys.platform,
  "ada_dot_all":        list(ada.__all__),
  "leaked_native_names": leaked,
  "backend":             backend.name,
  "box_aabb":            list(box_aabb),
  "n_box_tris":          n_box_tris,
  "cylinder_handle":     type(cyl).__name__,
  "sphere_handle":       type(sph).__name__,
  "n_cyl_tris":          n_cyl_tris,
  "n_sph_tris":          n_sph_tris,
  "from_topods_err":     ptr_err,
  "occ_backend_err":     occ_err,
  "bbox_box":            list(bb_box),
  "bbox_cyl":            list(bb_cyl),
  "bbox_sph":            list(bb_sph),
  "bbox_box_is_tuple":   isinstance(bb_box, tuple),
  "step_round_trip":     step_round_trip,
  "prim_round_trip":     prim_round_trip,
}
`);

    const r = result.toJs({ dict_converter: Object.fromEntries });
    result.destroy();
    console.log(JSON.stringify(r, null, 2));

    const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exit(1); };

    if (r.platform !== "emscripten") fail(`expected emscripten platform, got ${r.platform}`);
    if (r.leaked_native_names.length > 0) {
        fail(`native-only names leaked into pyodide ada: ${JSON.stringify(r.leaked_native_names)}`);
    }
    if (!Array.isArray(r.ada_dot_all) || !r.ada_dot_all.includes("cad")) {
        fail(`ada.__all__ should be ["cad"] under pyodide, got ${JSON.stringify(r.ada_dot_all)}`);
    }
    if (r.backend !== "adacpp") fail(`backend was ${r.backend}, expected adacpp`);

    const [xmin, xmax, ymin, ymax, zmin, zmax] = r.box_aabb;
    if (!approxEq(xmin, -1.0) || !approxEq(xmax, 1.0)) fail(`X AABB: ${xmin}..${xmax}`);
    if (!approxEq(ymin, -1.5) || !approxEq(ymax, 1.5)) fail(`Y AABB: ${ymin}..${ymax}`);
    if (!approxEq(zmin, -2.0) || !approxEq(zmax, 2.0)) fail(`Z AABB: ${zmin}..${zmax}`);
    if (r.n_box_tris < 12) fail(`box tri count low: ${r.n_box_tris}`);

    if (r.cylinder_handle !== "ShapeHandle") fail(`cyl handle: ${r.cylinder_handle}`);
    if (r.sphere_handle   !== "ShapeHandle") fail(`sph handle: ${r.sphere_handle}`);
    // Real OCCT tessellations now produce non-trivial triangle counts on wasm.
    // Default deflection: cylinder ~50-200 tris, sphere ~200-500 tris.
    if (!Number.isFinite(r.n_cyl_tris) || r.n_cyl_tris < 30) {
        fail(`cylinder tri count too low: ${r.n_cyl_tris}`);
    }
    if (!Number.isFinite(r.n_sph_tris) || r.n_sph_tris < 100) {
        fail(`sphere tri count too low: ${r.n_sph_tris}`);
    }
    // Null pointer reaches the C++ binding directly and bails out with a
    // RuntimeError. Any non-null pointer attempt would crash, so passing 0
    // is the only safe ptr to test.
    if (!r.from_topods_err || !r.from_topods_err.startsWith("RuntimeError")) {
        fail(`expected RuntimeError on null from_topods_pointer, got: ${r.from_topods_err}`);
    }
    // Either ImportError or its ModuleNotFoundError subclass is acceptable —
    // both signal "OccBackend can't initialize in pyodide", which is the point.
    if (!r.occ_backend_err ||
        !(r.occ_backend_err.startsWith("ImportError") || r.occ_backend_err.startsWith("ModuleNotFoundError"))) {
        fail(`expected (Module)ImportError when instantiating OccBackend in pyodide, got: ${r.occ_backend_err}`);
    }

    // bbox: AdacppBackend wraps adacpp.cad.bbox into a Python tuple — the
    // CadBackend protocol demands tuple, not list/array.
    if (!r.bbox_box_is_tuple) fail(`AdacppBackend.bbox should return a tuple`);

    const checkBbox = (actual, expected, label) => {
        if (actual.length !== 6) fail(`${label} bbox not length 6: ${JSON.stringify(actual)}`);
        for (let i = 0; i < 6; i++) {
            if (!approxEq(actual[i], expected[i])) {
                fail(`${label} bbox[${i}] = ${actual[i]}, expected ${expected[i]}`);
            }
        }
    };
    checkBbox(r.bbox_box, [-1.0, -1.5, -2.0, 1.0, 1.5, 2.0], "box");
    checkBbox(r.bbox_cyl, [-1.0, -1.0,  0.0, 1.0, 1.0, 5.0], "cyl");
    checkBbox(r.bbox_sph, [-2.0, -2.0, -2.0, 2.0, 2.0, 2.0], "sph");

    // Primitive → GLB: format magic 'glTF' + version 2.
    if (!r.prim_round_trip || r.prim_round_trip.magic !== "glTF" || r.prim_round_trip.version !== 2) {
        fail(`primitive GLB header wrong: ${JSON.stringify(r.prim_round_trip)}`);
    }
    if (r.prim_round_trip.output_bytes < 200) {
        fail(`primitive GLB suspiciously small: ${r.prim_round_trip.output_bytes} bytes`);
    }

    // STEP → GLB: only checked when fixture was mounted (was warned about
    // up top if not). Magic + version + reasonable size.
    if (r.step_round_trip) {
        if (r.step_round_trip.magic !== "glTF" || r.step_round_trip.version !== 2) {
            fail(`STEP→GLB header wrong: ${JSON.stringify(r.step_round_trip)}`);
        }
        if (r.step_round_trip.output_bytes < 1000) {
            fail(`STEP→GLB suspiciously small: ${r.step_round_trip.output_bytes} bytes`);
        }
    }

    console.log("\nOK — adapy.cad ↔ adacpp wasm path is working through the real ada.cad import.");
})().catch((e) => {
    console.error(e);
    process.exit(1);
});
