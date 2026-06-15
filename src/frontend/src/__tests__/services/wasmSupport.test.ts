import {test} from "node:test";
import assert from "node:assert/strict";

import {detectWasmFormat, isWasmFeaSource, wasmSupportsConversion} from "../../services/conversion/wasmSupport";

test("detectWasmFormat maps source extensions to the right pyodide stack", () => {
    assert.deepEqual(detectWasmFormat("models/a.ifc"), {format: "ifc", ext: "ifc"});
    assert.deepEqual(detectWasmFormat("models/a.STEP"), {format: "step", ext: "step"});
    assert.deepEqual(detectWasmFormat("models/a.stp"), {format: "step", ext: "stp"});
    assert.deepEqual(detectWasmFormat("models/a.sat"), {format: "sat", ext: "sat"});
    assert.deepEqual(detectWasmFormat("models/a.ACIS"), {format: "sat", ext: "acis"});
    assert.deepEqual(detectWasmFormat("models/a.obj"), {format: "mesh", ext: "obj"});
    assert.deepEqual(detectWasmFormat("models/a.STL"), {format: "mesh", ext: "stl"});
    assert.deepEqual(detectWasmFormat("a.ply"), {format: "mesh", ext: "ply"});
    assert.deepEqual(detectWasmFormat("a.gltf"), {format: "mesh", ext: "gltf"});
});

test("detectWasmFormat returns null for non-GLB / unsupported sources", () => {
    assert.equal(detectWasmFormat("a.glb"), null); // passthrough, not a conversion
    assert.equal(detectWasmFormat("a.rmed"), null); // FEA → bake path, not GLB
    assert.equal(detectWasmFormat("a.fem"), null);
    assert.equal(detectWasmFormat("noextension"), null);
});

test("isWasmFeaSource matches the FEA bake sources only", () => {
    for (const k of ["a.rmed", "x/y.MED", "z.sif", "w.sin"]) {
        assert.equal(isWasmFeaSource(k), true, k);
    }
    for (const k of ["a.step", "a.ifc", "a.obj", "a.glb", "a.fem"]) {
        assert.equal(isWasmFeaSource(k), false, k);
    }
});

test("wasmSupportsConversion requires a GLB target and a known source", () => {
    assert.equal(wasmSupportsConversion("a.step", "glb"), true);
    assert.equal(wasmSupportsConversion("a.obj", "glb"), true);
    // Non-GLB targets always route to the server worker.
    assert.equal(wasmSupportsConversion("a.step", "ifc"), false);
    assert.equal(wasmSupportsConversion("a.step", "step"), false);
    // Unknown source extension.
    assert.equal(wasmSupportsConversion("a.fem", "glb"), false);
});
