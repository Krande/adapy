// Persistent node-pyodide driver for the corpus WASM sweep (`ada audit
// wasm-sweep`). It loads ONE pyodide instance with the same wheels and runs
// the SAME Python entrypoints as the deployed browser worker
// (src/frontend/src/utils/pyodide/pyodide_worker.js), then streams cells:
//
//   stdin  : one JSON command per line
//              {"type":"cell","id":N,"format":"sat","ext":"sat",
//               "target":"glb","src":"/abs/path/to/source"}
//              {"type":"quit"}
//   stdout : one JSON record per line (the protocol the python parent reads)
//              {"type":"ready"}                         once stacks can load
//              {"type":"result","id":N,"ok":true,"ms":1234,"bytes":5678}
//              {"type":"result","id":N,"ok":false,"ms":12,"error":"..."}
//   stderr : free-text progress/log (kept off stdout so it stays clean JSONL)
//
// A cell that fatally aborts the wasm module (OOM / std::terminate) takes the
// whole process down — that's intentional: the parent notices the dropped
// result, records the in-flight cell as crashed, and restarts the driver to
// resume with the next cell. So this file never tries to recover from an
// abort; it just runs faithfully and lets the parent own isolation.
//
// Wheels are passed explicitly (--adacpp PATH --adapy PATH); the ifcopenshell
// wasm wheel is fetched from its canonical URL by micropip, exactly as the
// browser worker does. Mirror any worker dispatch change here.

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const {loadPyodide} = require("pyodide");

// Keep in lockstep with pyodide_worker.js.
const IFC_WASM_WHEEL =
    "https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl";
const MESH_EXTS = new Set(["obj", "stl", "ply", "gltf", "dae", "off", "glb"]);
const FEA_EXTS = new Set(["rmed", "med", "sif", "sin"]);

function arg(name, fallback) {
    const i = process.argv.indexOf(name);
    return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const ADACPP_WHEEL = arg("--adacpp");
const ADAPY_WHEEL = arg("--adapy");
const IFC_WHEEL = arg("--ifc-wheel", IFC_WASM_WHEEL);

function log(msg) {
    process.stderr.write(`[driver] ${msg}\n`);
}
function emit(obj) {
    process.stdout.write(JSON.stringify(obj) + "\n");
}

let pyodide = null;

// ── lazy stacks (mirror pyodide_worker.js ensure* fns) ────────────────────
let trimeshP = null;
let pyquatP = null;
let adacppP = null;
let adapyP = null;
let ifcP = null;
let stepP = null;
let satP = null;
let femP = null;
let meshP = null;

async function ensureTrimesh() {
    if (!trimeshP)
        trimeshP = (async () => {
            log("installing trimesh");
            await pyodide.runPythonAsync(`import micropip\nawait micropip.install("trimesh")`);
        })();
    return trimeshP;
}
async function ensurePyquaternion() {
    if (!pyquatP)
        pyquatP = (async () => {
            log("installing pyquaternion");
            await pyodide.runPythonAsync(`import micropip\nawait micropip.install("pyquaternion")`);
        })();
    return pyquatP;
}

// Stage a local wheel into the pyodide FS and micropip-install it from emfs:.
async function installLocalWheel(wheelPath, label, deps) {
    if (!wheelPath || !fs.existsSync(wheelPath)) {
        throw new Error(`${label} wheel not found: ${wheelPath}`);
    }
    pyodide.FS.mkdirTree("/wheels");
    const fsPath = "/wheels/" + path.basename(wheelPath);
    pyodide.FS.writeFile(fsPath, fs.readFileSync(wheelPath));
    pyodide.globals.set("_wheel_emfs", `emfs:${fsPath}`);
    pyodide.globals.set("_wheel_deps", deps);
    try {
        await pyodide.runPythonAsync(
            `import micropip\nawait micropip.install(_wheel_emfs, deps=_wheel_deps)`,
        );
    } finally {
        for (const g of ["_wheel_emfs", "_wheel_deps"]) {
            try {
                pyodide.globals.delete(g);
            } catch (_) {}
        }
    }
}

async function ensureAdacppWheel() {
    if (!adacppP)
        adacppP = (async () => {
            log(`installing adacpp wheel: ${path.basename(ADACPP_WHEEL)}`);
            await installLocalWheel(ADACPP_WHEEL, "adacpp", true);
        })();
    return adacppP;
}
async function ensureAdapyWheel() {
    if (!adapyP)
        adapyP = (async () => {
            log(`installing adapy wheel: ${path.basename(ADAPY_WHEEL)}`);
            await installLocalWheel(ADAPY_WHEEL, "adapy", false);
        })();
    return adapyP;
}

async function ensureIfcStack() {
    if (!ifcP)
        ifcP = (async () => {
            await ensureTrimesh();
            log("installing ifcopenshell (wasm wheel)");
            pyodide.globals.set("_ifc_wheel_url", IFC_WHEEL);
            try {
                await pyodide.runPythonAsync(`import micropip\nawait micropip.install(_ifc_wheel_url)`);
            } finally {
                try {
                    pyodide.globals.delete("_ifc_wheel_url");
                } catch (_) {}
            }
            await pyodide.runPythonAsync(`import ifcopenshell, ifcopenshell.geom, trimesh`);
        })();
    return ifcP;
}
async function ensureMeshStack() {
    if (!meshP)
        meshP = (async () => {
            await ensureTrimesh();
            await pyodide.runPythonAsync(`import trimesh`);
        })();
    return meshP;
}
async function ensureStepStack() {
    if (!stepP)
        stepP = (async () => {
            // Real ada/__init__.py: importing ada.cad runs the full init, which
            // eagerly imports trimesh + pyquaternion — provide them first.
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            await pyodide.runPythonAsync(
                `import ada.cad\nada.cad.select_backend(prefer="adacpp")`,
            );
        })();
    return stepP;
}
async function ensureSatStack() {
    if (!satP)
        satP = (async () => {
            log("installing SAT stack (pydantic + Pillow)");
            await pyodide.loadPackage(["pydantic", "Pillow"]);
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            await pyodide.runPythonAsync(
                `import ada.cad\nada.cad.select_backend(prefer="adacpp")\nimport ada`,
            );
        })();
    return satP;
}
async function ensureFemStack() {
    if (!femP)
        femP = (async () => {
            // pydantic: ada's FEM-deck import path (ada.from_fem → concept
            // build) pulls it, same as the SAT stack — without it FEM-deck
            // cells fail with ModuleNotFoundError: pydantic.
            log("installing FEM stack (h5py + pydantic + Pillow)");
            await pyodide.loadPackage(["h5py", "pydantic", "Pillow"]);
            await ensureTrimesh();
            await ensurePyquaternion();
            await ensureAdacppWheel();
            await ensureAdapyWheel();
            await pyodide.runPythonAsync(
                `import ada.cad\nada.cad.select_backend(prefer="adacpp")\n` +
                    `from ada.fem.results.artefacts import bake_fea_artefacts_from_source`,
            );
        })();
    return femP;
}

// ── stack selection (host-specific) + dispatch into adapy ─────────────────
// All conversion LOGIC lives in adapy (ada.cadit.wasm_convert), shipped in the
// wheel and shared verbatim with the browser worker — no duplicated Python
// here. We only install the right packages/wheels for a cell, then call run().

async function ensureStacks(format, target) {
    const tgt = (target || "glb").toLowerCase();
    if (format === "fea") return ensureFemStack();
    if (format === "fea_glb") return ensureFemStack(); // SIF/SIN result → single GLB
    if (format === "mesh") return ensureMeshStack();
    if (format === "ifc" && tgt === "glb") return ensureIfcStack(); // raw ifcopenshell fast path
    if (format === "step" && tgt === "glb") return ensureStepStack(); // adacpp fast path
    if (format === "fem") await ensureFemStack();
    else await ensureSatStack();
    if (format === "ifc" || tgt === "ifc") await ensureIfcStack();
}

async function runCell(cell) {
    const format = (cell.format || "").toLowerCase();
    const target = (cell.target || "glb").toLowerCase();
    const ext = (cell.ext || "").toLowerCase();
    // fs.readFileSync returns a node Buffer; pyodide rejects it — copy into a
    // plain Uint8Array (matches what the browser worker hands pyodide).
    const raw = fs.readFileSync(cell.src);
    const u8 = new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength);
    await ensureStacks(format, target);
    const inPath = `/tmp/input.${ext || "bin"}`;
    pyodide.FS.writeFile(inPath, u8);
    pyodide.globals.set("_wc_fmt", format);
    pyodide.globals.set("_wc_ext", ext);
    pyodide.globals.set("_wc_target", target);
    pyodide.globals.set("_wc_src", inPath);
    try {
        // Return only the output length — keeps big buffers out of JS (the
        // sweep only needs pass/fail + size).
        return await pyodide.runPythonAsync(`
import ada.cadit.wasm_convert as _wc
len(_wc.run(_wc_fmt, _wc_ext, _wc_target, _wc_src))
`);
    } finally {
        for (const g of ["_wc_fmt", "_wc_ext", "_wc_target", "_wc_src"]) {
            try { pyodide.globals.delete(g); } catch (_) {}
        }
    }
}

(async () => {
    if (!ADACPP_WHEEL || !ADAPY_WHEEL) {
        log("missing --adacpp and/or --adapy wheel path");
        process.exit(2);
    }
    log("booting pyodide");
    pyodide = await loadPyodide();
    // Load Pillow before any `import ada` (→ first `import trimesh`): trimesh
    // probes for PIL at import time, so importing it without Pillow (e.g. a
    // STEP cell) caches "no PIL" and later textured GLB exports fail in
    // trimesh._append_material. Up-front load makes cell order irrelevant.
    await pyodide.loadPackage(["micropip", "numpy", "Pillow"]);
    emit({type: "ready"});

    const rl = readline.createInterface({input: process.stdin});
    for await (const line of rl) {
        const text = line.trim();
        if (!text) continue;
        let cmd;
        try {
            cmd = JSON.parse(text);
        } catch (e) {
            log(`bad command JSON: ${text}`);
            continue;
        }
        if (cmd.type === "quit") break;
        if (cmd.type !== "cell") continue;
        const t0 = performance.now();
        try {
            const out = await runCell(cmd);
            const bytes = typeof out === "number" ? out : Number(out);
            emit({type: "result", id: cmd.id, ok: true, ms: Math.round(performance.now() - t0), bytes});
        } catch (err) {
            emit({
                type: "result",
                id: cmd.id,
                ok: false,
                ms: Math.round(performance.now() - t0),
                error: String((err && err.message) || err).slice(0, 2000),
            });
        }
    }
    process.exit(0);
})().catch((err) => {
    log(`fatal: ${err && err.stack ? err.stack : err}`);
    process.exit(1);
});
