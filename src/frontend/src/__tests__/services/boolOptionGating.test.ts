/** A bool conversion option must be offered only where the backend can honour it.
 *
 * The "Clickable surfaces" reconvert toggle asks for per-face pick regions, which ONLY the native
 * STEP→GLB path produces — the python path never reads the flag. So the backend advertises
 * `supported_by` on the option and the SPA gates on it. Testing the serializer token here instead
 * ("is it 'cpp'?") would hardcode a vocabulary the frontend is supposed to only render, and would
 * drift the moment another path learns to emit regions.
 *
 * The failure this guards is silent: a toggle offered against a path that ignores it returns a GLB
 * with no regions and reports success.
 */
import {test} from "node:test";
import assert from "node:assert/strict";

import {boolOptionFor, boolOptionSupported} from "../../services/conversion/serializerMatrix";

/** runtime.conversionOptionsFor reads window.CONVERSION_MATRIX; install one. */
function installMatrix(supported_by: string[] | undefined): void {
    (globalThis as Record<string, unknown>).window = globalThis;
    (globalThis as Record<string, unknown>).CONVERSION_MATRIX = [
        {
            from: ".stp",
            to: ["glb"],
            options: {
                glb: [
                    {
                        name: "serializer",
                        type: "enum",
                        default: "cpp",
                        enum: ["cpp", "python"],
                        runtime: {cpp: "server", python: "server"},
                    },
                    {
                        name: "tessellator",
                        type: "enum",
                        default: "adacpp:libtess2",
                        enum: ["adacpp:libtess2", "adacpp:cdt"],
                        enum_by: {cpp: ["adacpp:libtess2", "adacpp:cdt"], python: ["adacpp:libtess2"]},
                        depends_on: "serializer",
                    },
                    {
                        name: "face_regions",
                        type: "bool",
                        title: "Clickable surfaces",
                        default: false,
                        depends_on: "serializer",
                        ...(supported_by === undefined ? {} : {supported_by}),
                    },
                ],
            },
        },
    ];
}

test("the option is found only where the backend advertises it", () => {
    installMatrix(["cpp"]);
    assert.equal(boolOptionFor(".stp", "glb", "face_regions")?.title, "Clickable surfaces");
    assert.equal(boolOptionFor(".ifc", "glb", "face_regions"), null, "not advertised on this row");
    assert.equal(boolOptionFor(".stp", "glb", "nope"), null);
});

test("supported only for the serializers the backend lists", () => {
    installMatrix(["cpp"]);
    assert.equal(boolOptionSupported(".stp", "glb", "face_regions", {serializer: "cpp"}), true);
    assert.equal(
        boolOptionSupported(".stp", "glb", "face_regions", {serializer: "python"}),
        false,
        "python ignores the flag — offering it there would report regions that were never embedded",
    );
});

test("an empty supported_by means nowhere, not everywhere", () => {
    installMatrix([]);
    assert.equal(boolOptionSupported(".stp", "glb", "face_regions", {serializer: "cpp"}), false);
    assert.equal(boolOptionSupported(".stp", "glb", "face_regions", {serializer: "python"}), false);
});

test("an absent supported_by means every serializer can honour it", () => {
    installMatrix(undefined);
    assert.equal(boolOptionSupported(".stp", "glb", "face_regions", {serializer: "cpp"}), true);
    assert.equal(boolOptionSupported(".stp", "glb", "face_regions", {serializer: "python"}), true);
});

test("an empty selection resolves to the default serializer before gating", () => {
    // The gallery starts with {} until a dropdown is touched; normalizeSelection falls back to the
    // advertised default (cpp), which is the serializer the reconvert would actually run.
    installMatrix(["cpp"]);
    assert.equal(boolOptionSupported(".stp", "glb", "face_regions", {}), true);
});
