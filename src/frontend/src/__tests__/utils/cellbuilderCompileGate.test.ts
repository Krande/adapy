import assert from "node:assert/strict";
import {test} from "node:test";

import {needsPreviewCompile, type CompileGateState} from "../../utils/cellbuilder/compileGate";

const base: CompileGateState = {
    dirty: false,
    buildSim: true,
    buildDetail: false,
    resultSourceName: null,
    detailSourceName: null,
};

test("dirty always needs a compile", () => {
    assert.equal(needsPreviewCompile({...base, dirty: true, resultSourceName: "x"}), true);
});

test("clean with no sim result loaded still needs a compile", () => {
    // The reported bug: ⇧↵ on an unchanged model with nothing in the scene did
    // nothing. Now it compiles because the wanted sim result is missing.
    assert.equal(needsPreviewCompile(base), true);
});

test("clean with the sim result loaded is a no-op", () => {
    assert.equal(needsPreviewCompile({...base, resultSourceName: "procedural:m"}), false);
});

test("buildSim off + buildDetail on wants detail, not sim", () => {
    const s: CompileGateState = {...base, buildSim: false, buildDetail: true};
    // sim result present but detail missing → still needs a compile
    assert.equal(needsPreviewCompile({...s, resultSourceName: "procedural:m"}), true);
    // detail present → no-op
    assert.equal(needsPreviewCompile({...s, detailSourceName: "procedural-detail:m"}), false);
});

test("both LODs wanted: needs a compile until both are loaded", () => {
    const s: CompileGateState = {...base, buildSim: true, buildDetail: true};
    assert.equal(needsPreviewCompile(s), true);
    assert.equal(needsPreviewCompile({...s, resultSourceName: "procedural:m"}), true);
    assert.equal(
        needsPreviewCompile({
            ...s,
            resultSourceName: "procedural:m",
            detailSourceName: "procedural-detail:m",
        }),
        false,
    );
});

test("neither build flag falls back to wanting sim", () => {
    // compilePreviewSelected defaults to sim when neither flag is on.
    const s: CompileGateState = {...base, buildSim: false, buildDetail: false};
    assert.equal(needsPreviewCompile(s), true);
    assert.equal(needsPreviewCompile({...s, resultSourceName: "procedural:m"}), false);
});
