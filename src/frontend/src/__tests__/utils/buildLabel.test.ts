// What the "Build:" line says, and why each branch says it.
//
// Every branch here produces a plausible string, which is why this is tested
// rather than eyeballed: a wrong one does not fail, it just tells the reader
// something untrue about what they are running.

import assert from "node:assert/strict";
import { test } from "node:test";

import { adapyRefStamp, buildLabel, buildStamp } from "../../utils/buildLabel";

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

// --- the adapy ref -----------------------------------------------------------
//
// The case none of the above can see. A branch cut from v0.64.1 with no version
// bump reports version 0.64.1, and the image tag is the ASSEMBLING repo's commit
// and run number — so a viewer built from a feature branch and one built from
// the release render identically, character for character.

test("a ref that only repeats the version is not shown", () => {
    // Both spellings: the manifest pins `v0.64.1`, a caller might pass `0.64.1`.
    assert.equal(adapyRefStamp("0.64.1", "v0.64.1"), "");
    assert.equal(adapyRefStamp("0.64.1", "0.64.1"), "");
    assert.equal(buildLabel("0.64.1", "", "sha-0f54f92-44", 7, "v0.64.1"), "0.64.1 (0f54f92-44)");
});

test("a ref the version does not imply IS shown", () => {
    // The exact shape that caused a wrong conclusion about what was deployed.
    assert.equal(
        buildLabel("0.64.1", "", "sha-0f54f92-44", 7, "feat/source-nodes-write-route"),
        "0.64.1 (0f54f92-44, adapy feat/source-nodes-write-route)",
    );
});

test("a resolved commit rides along without changing what is suppressed", () => {
    // A branch moves, so the ref is stamped as `<ref>@<sha>`. Only the NAME is
    // compared — otherwise appending the sha would be what made every release
    // build start printing a ref that says nothing.
    assert.equal(adapyRefStamp("0.64.1", "v0.64.1@abc1234"), "");
    assert.equal(
        adapyRefStamp("0.64.1", "feat/source-nodes-write-route@427b39bd"),
        "feat/source-nodes-write-route@427b39bd",
    );
});

test("a ref with no other provenance still qualifies the line", () => {
    assert.equal(buildLabel("0.64.1", "", "", 7, "some-branch"), "0.64.1 (adapy some-branch)");
    assert.equal(buildLabel("", "", "", 7, "some-branch"), "adapy some-branch");
});

test("no ref changes nothing, so every existing build renders as before", () => {
    assert.equal(adapyRefStamp("0.64.1", ""), "");
    assert.equal(adapyRefStamp("0.64.1", "   "), "");
    assert.equal(buildLabel("0.61.0", "43ae2883", "v1.2.1", 7), "0.61.0 (43ae2883)");
});
