/** What the viewer will try to open, and what it will not.
 *
 * The bug this pins down: a `csg.db` sitting in a scope was walked by the
 * gallery and offered a load checkbox by the storage browser, and could only
 * fail — nothing converts it. The cause was `canLoadIntoSceneLegacy` being a
 * DENYLIST ("everything except .rmed and the streaming-only extensions") while
 * its comment claimed to mirror `supported_targets_for`, which answers "the
 * targets this extension has" and therefore says NO to everything it has never
 * heard of. `.db` was never the point: every unknown extension was affected.
 *
 * The other half is the interaction. Tightening the predicate must not hide the
 * very files a plugin has taught the viewer to render, so `canOpenInScene` also
 * asks the plugin registry. Both directions are asserted here, because getting
 * one right and the other wrong ships two changes that cancel.
 */
import {beforeEach, test} from "node:test";
import assert from "node:assert/strict";

import {registerPlugin, resetRegistry} from "@/plugins/registry";
import {
    canLoadIntoSceneLegacy,
    canOpenInScene,
    isStreamingFEAResult,
} from "@/utils/scene/fileKinds";

/** The runtime config reads `window.*`; under node --test there is no window. */
function installConfig(opts: {
    convertEnabled?: boolean;
    matrix?: {from: string; to: string[]}[];
    streamingOnly?: string[];
}): void {
    const g = globalThis as Record<string, unknown>;
    g.window = globalThis;
    g.CONVERT_ENABLED = opts.convertEnabled ?? true;
    g.CONVERSION_MATRIX = opts.matrix ?? [];
    g.STREAMING_ONLY_EXTS = opts.streamingOnly ?? [];
}

/** The matrix a live deployment publishes, trimmed to the rows under test.
 * Transcribed from `/api/config` on a stack with an adapy worker attached. */
const LIVE_MATRIX = [
    {from: ".ifc", to: ["glb", "gnx", "ifc", "obj", "step", "stl", "xml"]},
    {from: ".stp", to: ["glb", "gnx", "ifc", "obj", "step", "stl", "xml"]},
    {from: ".glb", to: ["glb", "obj", "stl"]},
    {from: ".sif", to: ["glb"]},
    // .med converts to FE decks and to NOTHING renderable — the one extension
    // that separates "has any target" from "has a GLB target".
    {from: ".med", to: ["fem", "inp"]},
    {from: ".inp", to: ["fem", "glb", "gnx", "ifc", "med", "obj", "step", "stl", "xml"]},
];

beforeEach(() => {
    resetRegistry();
    installConfig({matrix: LIVE_MATRIX});
});

test("an extension with no GLB target is not legacy-loadable", () => {
    // The report: csg.db files in a scope.
    assert.equal(canLoadIntoSceneLegacy("exports/csg.db"), false);
    // ...and the class it belongs to, which is the actual fix.
    assert.equal(canLoadIntoSceneLegacy("notes.txt"), false);
    assert.equal(canLoadIntoSceneLegacy("archive.tar.gz"), false);
    assert.equal(canLoadIntoSceneLegacy("model.unknownext"), false);
    // A key with no extension at all (a folder marker) is not a file to open.
    assert.equal(canLoadIntoSceneLegacy("some/folder"), false);
    // A dot in a FOLDER name must not be read as the key's extension.
    assert.equal(canLoadIntoSceneLegacy("2026.05/manifest"), false);
    // Has targets, but none of them renderable.
    assert.equal(canLoadIntoSceneLegacy("mesh.med"), false);
});

test("the formats the pipeline really converts stay loadable", () => {
    for (const key of ["a.ifc", "a.stp", "a.glb", "a.sif", "a.inp"]) {
        assert.equal(canLoadIntoSceneLegacy(key), true, key);
    }
    // Case is not significant in a storage key's extension.
    assert.equal(canLoadIntoSceneLegacy("A.IFC"), true);
});

test("a bundle inherits the inner deck's targets, as the server does", () => {
    // `.zip` is in no worker's matrix — the server answers for it by delegating
    // to `.inp` (supported_targets_for's _BUNDLE_EXTS branch). A client reading
    // the matrix literally would call every analysis bundle unloadable.
    assert.equal(canLoadIntoSceneLegacy("run.zip"), true);
});

test("streaming-only extensions stay off the legacy path", () => {
    installConfig({matrix: LIVE_MATRIX, streamingOnly: [".odb", ".rmed", ".sqlite"]});
    assert.equal(canLoadIntoSceneLegacy("job.odb"), false);
    assert.equal(canLoadIntoSceneLegacy("res.rmed"), false);
    // ...but they are still openable, by the streaming bake.
    assert.equal(isStreamingFEAResult("job.odb"), true);
    assert.equal(canOpenInScene("job.odb"), true);
});

test("with no conversion queue only a GLB is loadable", () => {
    // `overlay_file_in_scene` refuses a non-GLB source when convert is
    // disabled, so a checkbox offering one would silently do nothing.
    installConfig({convertEnabled: false, matrix: []});
    assert.equal(canLoadIntoSceneLegacy("a.glb"), true);
    assert.equal(canLoadIntoSceneLegacy("a.ifc"), false);
});

test("with convert on but no worker registered, the static mirror answers", () => {
    // A page that loaded before any worker reported in has an empty matrix.
    // Falling through to "nothing is loadable" would empty the storage browser
    // until the next reload.
    installConfig({matrix: []});
    assert.equal(canLoadIntoSceneLegacy("a.ifc"), true);
    assert.equal(canLoadIntoSceneLegacy("a.step"), true);
    assert.equal(canLoadIntoSceneLegacy("run.zip"), true);
    // The mirror is still an allowlist: the bug does not come back here.
    assert.equal(canLoadIntoSceneLegacy("exports/csg.db"), false);
    assert.equal(canLoadIntoSceneLegacy("mesh.med"), false);
});

test("with no plugins, canOpenInScene is exactly core's two routes", () => {
    assert.equal(canOpenInScene("a.ifc"), true);
    assert.equal(canOpenInScene("a.sif"), true);
    assert.equal(canOpenInScene("exports/csg.db"), false);
    assert.equal(canOpenInScene("notes.txt"), false);
});

test("a plugin provider makes its file kind openable again", () => {
    registerPlugin({
        id: "renderer",
        renderableFileProviders: [
            {
                id: "csg",
                claims: (key) => key.endsWith("/csg.db"),
                open: async () => {},
            },
        ],
    });
    // The interaction in one line: the allowlist says no, the plugin says yes,
    // and the file is offered.
    assert.equal(canLoadIntoSceneLegacy("assets/c/s/r/csg.db"), false);
    assert.equal(canOpenInScene("assets/c/s/r/csg.db"), true);
    // A claim is not a licence over everything: what the provider does not
    // claim stays closed.
    assert.equal(canOpenInScene("assets/c/s/r/other.db"), false);
    assert.equal(canOpenInScene("notes.txt"), false);
});

test("a disabled plugin's claims stop counting", () => {
    registerPlugin({
        id: "renderer",
        // A throwing predicate disables the plugin; the file must not become
        // openable, and every OTHER file must stay openable.
        renderableFileProviders: [
            {
                id: "csg",
                claims: () => {
                    throw new Error("boom");
                },
                open: async () => {},
            },
        ],
    });
    assert.equal(canOpenInScene("assets/c/s/r/csg.db"), false);
    assert.equal(canOpenInScene("a.ifc"), true);
});
