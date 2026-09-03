// What the "Build:" line says, and why each branch says it.
//
// Every branch here produces a plausible string, which is why this is tested
// rather than eyeballed: a wrong one does not fail, it just tells the reader
// something untrue about what they are running.

import assert from "node:assert/strict";
import { test } from "node:test";

import { buildLabel, buildStamp } from "../../utils/buildLabel";

test("a build-time git sha wins, because it is the most precise answer", () => {
    assert.equal(buildStamp("43ae2883", "v1.2.1"), "43ae2883");
    assert.equal(buildLabel("0.61.0", "43ae2883", "v1.2.1", 7), "0.61.0 (43ae2883)");
});

test("a sha- tag is unwrapped so both provenance paths read alike", () => {
    assert.equal(buildStamp("", "sha-4fe483c"), "4fe483c");
    assert.equal(buildLabel("0.61.0", "", "sha-4fe483c", 7), "0.61.0 (4fe483c)");
});

test("a release tag is KEPT, not discarded", () => {
    // The regression this exists for. Only `sha-` tags used to be recognised,
    // so an image built from a release tag rendered as the package version
    // alone -- which cannot tell apart two images built from the same package
    // release with different contents.
    assert.equal(buildStamp("", "v1.2.1"), "v1.2.1");
    assert.equal(buildLabel("0.61.0", "", "v1.2.1", 7), "0.61.0 (v1.2.1)");
});

test("no provenance at all leaves the version standing alone", () => {
    assert.equal(buildStamp("", ""), "");
    assert.equal(buildLabel("0.61.0", "", "", 7), "0.61.0");
    // Whitespace is not provenance.
    assert.equal(buildLabel("0.61.0", "", "   ", 7), "0.61.0");
});

test("with no version, the stamp carries the line", () => {
    assert.equal(buildLabel("", "", "v1.2.1", 7), "v1.2.1");
    assert.equal(buildLabel("", "43ae2883", "", 7), "43ae2883");
});

test("a build with neither still renders something a bug report can quote", () => {
    assert.equal(buildLabel("", "", "", 7), "7");
});
