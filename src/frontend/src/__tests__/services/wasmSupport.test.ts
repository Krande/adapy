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
    // glb is a mesh source (glb→obj/stl); glb→glb is excluded as a no-op below.
    assert.deepEqual(detectWasmFormat("a.glb"), {format: "mesh", ext: "glb"});
    // FEM decks read via ada.from_fem.
    assert.deepEqual(detectWasmFormat("a.inp"), {format: "fem", ext: "inp"});
    assert.deepEqual(detectWasmFormat("a.fem"), {format: "fem", ext: "fem"});
    // Genie xml.
    assert.deepEqual(detectWasmFormat("a.xml"), {format: "genie", ext: "xml"});
    // Sesam SIF/SIN results → single GLB via FEAResult.to_gltf (fea_glb stack).
    assert.deepEqual(detectWasmFormat("a.sif"), {format: "fea_glb", ext: "sif"});
    assert.deepEqual(detectWasmFormat("a.SIN"), {format: "fea_glb", ext: "sin"});
});

test("detectWasmFormat returns null for unsupported / bake-only sources", () => {
    // .rmed/.med have no single-GLB conversion cell — they're bake-only
    // (isWasmFeaSource), reached via the FEA-viewer flow, not this matrix.
    assert.equal(detectWasmFormat("a.rmed"), null);
    assert.equal(detectWasmFormat("a.med"), null);
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

test("wasmSupportsConversion consults the per-source target matrix", () => {
    assert.equal(wasmSupportsConversion("a.step", "glb"), true);
    assert.equal(wasmSupportsConversion("a.obj", "glb"), true);
    // Non-GLB writers route in-browser for the sources that support them.
    assert.equal(wasmSupportsConversion("a.step", "ifc"), true);
    assert.equal(wasmSupportsConversion("a.step", "step"), true); // genuine writer round-trip
    assert.equal(wasmSupportsConversion("a.fem", "glb"), true);
    // No-op self-conversions aren't real conversions.
    assert.equal(wasmSupportsConversion("a.glb", "glb"), false);
    assert.equal(wasmSupportsConversion("a.fem", "fem"), false);
    // Unknown source extension.
    assert.equal(wasmSupportsConversion("a.unknown", "glb"), false);
});

test("wasmSupportsConversion covers SIF/SIN result → GLB (and only GLB)", () => {
    // The regression this guards: the WASM audit sweep used to skip .sif/.sin
    // cells because they were known only as bake sources, never as producers
    // of their lone registry target (glb). read_sif/read_sin → FEAResult
    // .to_gltf is pure-python+numpy+trimesh, so the engine can serve them.
    assert.equal(wasmSupportsConversion("results/a.sif", "glb"), true);
    assert.equal(wasmSupportsConversion("results/a.SIN", "glb"), true);
    // FEA results have no other in-browser conversion target.
    assert.equal(wasmSupportsConversion("results/a.sin", "ifc"), false);
    assert.equal(wasmSupportsConversion("results/a.sif", "step"), false);
});
