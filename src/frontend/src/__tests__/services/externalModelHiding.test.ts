import assert from "node:assert/strict";
import {test} from "node:test";

import {
    bindingFor,
    isHidden,
    matchesGlob,
    parseBindingMap,
    serialiseBinding,
} from "@/services/externalModelsBinding";

// Hiding models a scope does not care about, and the three shapes a binding can
// be stored in.
//
// WHY IT MATTERS MORE THAN IT LOOKS: "Load all" acts on what is visible, so a
// filter that silently matches nothing pulls in exactly the models someone had
// declared irrelevant -- the temporary-steel exports, the volume models -- and
// each is tens of megabytes.

const model = (over: Record<string, unknown> = {}) => ({
    id: "ModelExportMain.rvm~AP400-STRU_MS",
    name: "/AP400-STRU_MS",
    description: "ModelExportMain.rvm · ASP",
    ...over,
});

// --- the stored shapes ------------------------------------------------------

test("a bare collection is a binding on the default provider", () => {
    const map = parseBindingMap(JSON.stringify({shared: "asp"}));
    assert.deepEqual(bindingFor(map, "shared"), {
        provider: "demo",
        collection: "asp",
        hide: [],
    });
});

test("provider:collection still parses, and carries no filter", () => {
    const map = parseBindingMap(JSON.stringify({shared: "web3d:asp"}));
    assert.deepEqual(bindingFor(map, "shared"), {
        provider: "web3d",
        collection: "asp",
        hide: [],
    });
});

test("the object form carries the filter", () => {
    const map = parseBindingMap(
        JSON.stringify({shared: {provider: "web3d", collection: "asp", hide: ["TempSteel"]}}),
    );
    assert.deepEqual(bindingFor(map, "shared"), {
        provider: "web3d",
        collection: "asp",
        hide: ["TempSteel"],
    });
});

test("a binding with no filter is written back as the short string", () => {
    // Turning the filter ON is the only thing that changes an entry's shape,
    // and turning it off changes it back. Otherwise adding this feature would
    // have rewritten every existing binding into a longer form meaning the same.
    assert.equal(serialiseBinding({provider: "web3d", collection: "asp"}), "web3d:asp");
    assert.equal(serialiseBinding({provider: "web3d", collection: "asp", hide: ["  "]}), "web3d:asp");
    assert.deepEqual(serialiseBinding({provider: "web3d", collection: "asp", hide: ["x"]}), {
        provider: "web3d",
        collection: "asp",
        hide: ["x"],
    });
});

test("an unrecognised row is dropped rather than guessed at", () => {
    // A half-understood binding pointing somewhere unintended is worse than an
    // unbound scope, which at least says so on screen.
    const map = parseBindingMap(JSON.stringify({a: 42, b: {hide: ["x"]}, c: "web3d:asp"}));
    assert.deepEqual(Object.keys(map), ["c"]);
});

// --- matching ---------------------------------------------------------------

test("a pattern with no wildcard is a substring", () => {
    // What typing "Temp" into a filter box means to everyone not thinking
    // about globs.
    assert.ok(matchesGlob("ModelExportTempSteel.rvm", "Temp"));
    assert.ok(!matchesGlob("ModelExportMain.rvm", "Temp"));
});

test("matching is case-insensitive", () => {
    assert.ok(matchesGlob("ModelExportTempSteel.rvm", "tempsteel"));
});

test("* and ? are wildcards and anchor the whole string", () => {
    assert.ok(matchesGlob("/AP400-ELEC_VAT", "*_VAT"));
    assert.ok(!matchesGlob("/AP400-ELEC_VAT_X", "*_VAT"));
    assert.ok(matchesGlob("/AP400-STRU", "/AP4?0-STRU"));
});

test("regex metacharacters are literal, not syntax", () => {
    // `/AP400(511)-STRU` is an ordinary E3D name. Treating the parentheses as a
    // group would make the pattern match something else entirely, or throw.
    assert.ok(matchesGlob("/AP400(511)-STRU", "*(511)*"));
    assert.ok(!matchesGlob("/AP400511-STRU", "*(511)*"));
    assert.ok(matchesGlob("a.b", "a.b"));
});

// --- what gets hidden -------------------------------------------------------

test("no patterns hides nothing", () => {
    assert.equal(isHidden(model(), []), false);
});

test("a pattern is matched against the name, the id AND the description", () => {
    // They carry different halves of what an admin is looking at: web3d names a
    // model for its SITE and puts the RVM export in the description, so
    // "hide the temporary steel" is a description pattern while "hide the VAT
    // sites" is a name one. Requiring them to know which would make the box
    // fail silently half the time.
    assert.ok(isHidden(model({description: "ModelExportTempSteel.rvm · ASP"}), ["TempSteel"]));
    assert.ok(isHidden(model({name: "/AP400-ELEC_VAT"}), ["*_VAT"]));
    assert.ok(isHidden(model({id: "ModelExportVolumes.rvm~A000-AREAS"}), ["*Volumes*"]));
});

test("a model matching none of several patterns survives", () => {
    assert.equal(isHidden(model(), ["TempSteel", "*_VAT", "*Volumes*"]), false);
});

test("a model with no description is still matched on what it has", () => {
    assert.ok(isHidden(model({description: null}), ["AP400-STRU_MS"]));
});
