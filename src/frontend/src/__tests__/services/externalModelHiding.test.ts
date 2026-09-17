import assert from "node:assert/strict";
import {test} from "node:test";

import {
    bindingFor,
    isHidden,
    isPattern,
    matchesGlob,
    parseBindingMap,
    serialiseBinding,
} from "@/services/externalModelsBinding";

// Hiding models a scope does not care about, and the three shapes a binding can
// be stored in.
//
// WHY IT MATTERS MORE THAN IT LOOKS: "Load all" acts on what is visible, so a
// filter that silently matches nothing pulls in exactly the models someone had
// declared irrelevant, and a model can be tens of megabytes.

const model = (over: Record<string, unknown> = {}) => ({
    id: "export-a.rvm~site-one",
    name: "/site-one",
    description: "export-a.rvm · demo",
    ...over,
});

// --- the stored shapes ------------------------------------------------------

test("a bare collection is a binding on the default provider", () => {
    const map = parseBindingMap(JSON.stringify({shared: "alpha"}));
    assert.deepEqual(bindingFor(map, "shared"), {
        provider: "demo",
        collection: "alpha",
        hide: [],
    });
});

test("provider:collection still parses, and carries no filter", () => {
    const map = parseBindingMap(JSON.stringify({shared: "vendor:alpha"}));
    assert.deepEqual(bindingFor(map, "shared"), {
        provider: "vendor",
        collection: "alpha",
        hide: [],
    });
});

test("the object form carries the filter", () => {
    const map = parseBindingMap(
        JSON.stringify({shared: {provider: "vendor", collection: "alpha", hide: ["*draft*"]}}),
    );
    assert.deepEqual(bindingFor(map, "shared"), {
        provider: "vendor",
        collection: "alpha",
        hide: ["*draft*"],
    });
});

test("a binding with no filter is written back as the short string", () => {
    // Turning the filter ON is the only thing that changes an entry's shape,
    // and turning it off changes it back. Otherwise adding this feature would
    // have rewritten every existing binding into a longer form meaning the same.
    assert.equal(serialiseBinding({provider: "vendor", collection: "alpha"}), "vendor:alpha");
    assert.equal(serialiseBinding({provider: "vendor", collection: "alpha", hide: ["  "]}), "vendor:alpha");
    assert.deepEqual(serialiseBinding({provider: "vendor", collection: "alpha", hide: ["x"]}), {
        provider: "vendor",
        collection: "alpha",
        hide: ["x"],
    });
});

test("an unrecognised row is dropped rather than guessed at", () => {
    // A half-understood binding pointing somewhere unintended is worse than an
    // unbound scope, which at least says so on screen.
    const map = parseBindingMap(JSON.stringify({a: 42, b: {hide: ["x"]}, c: "vendor:alpha"}));
    assert.deepEqual(Object.keys(map), ["c"]);
});

// --- matching ---------------------------------------------------------------

test("an entry is a pattern only when it carries a wildcard", () => {
    // Which kind an entry is decides how it matches, and the UI writes only the
    // first kind.
    assert.equal(isPattern("export-a.rvm~site-one"), false);
    assert.equal(isPattern("*draft*"), true);
    assert.equal(isPattern("AP4?0"), true);
});

test("an id is matched EXACTLY, because these ids nest", () => {
    // `...~site` is a prefix of `...~site-one`. A substring rule
    // would tick one site and hide two, and the second one is simply absent
    // with nothing saying why.
    const shorter = model({id: "export-a.rvm~site", name: "/site"});
    const longer = model({id: "export-a.rvm~site-one", name: "/site-one"});
    const hide = ["export-a.rvm~site"];

    assert.equal(isHidden(shorter, hide), true);
    assert.equal(isHidden(longer, hide), false, "the longer id is a different model");
});

test("an exact entry does not match the name or the description", () => {
    // Only a pattern searches those. An id that happened to appear inside a
    // description would otherwise hide a model the admin never ticked.
    assert.equal(isHidden(model({id: "other"}), ["/site-one"]), false);
});

test("matching is case-insensitive", () => {
    assert.ok(matchesGlob("export-b.rvm", "*port-b*"));
    assert.ok(isHidden(model({id: "export-a.rvm~site-one"}), ["EXPORT-A.RVM~SITE-ONE"]));
});

test("* and ? are wildcards and anchor the whole string", () => {
    assert.ok(matchesGlob("/site-two_VAT", "*_VAT"));
    assert.ok(!matchesGlob("/site-two_VAT_X", "*_VAT"));
    assert.ok(matchesGlob("/site-one", "/site-o?e"));
});

test("regex metacharacters are literal, not syntax", () => {
    // `/site(511)-x` is an ordinary name in some catalogues. Treating the parentheses as a
    // group would make the pattern match something else entirely, or throw.
    assert.ok(matchesGlob("/site(511)-x", "*(511)*"));
    assert.ok(!matchesGlob("/site511-x", "*(511)*"));
    assert.ok(matchesGlob("a.b", "a.b"));
});

// --- what gets hidden -------------------------------------------------------

test("no patterns hides nothing", () => {
    assert.equal(isHidden(model(), []), false);
});

test("a WILDCARD pattern is matched against the name, the id AND the description", () => {
    // They carry different halves of what an admin is looking at: a catalogue may name a model for
    // its SITE and put the source export in the description, so "hide that
    // export" is a description pattern while "hide the VAT sites" is a name one. Requiring them to know which would make the box
    // fail silently half the time.
    assert.ok(isHidden(model({description: "export-b.rvm · demo"}), ["*port-b*"]));
    assert.ok(isHidden(model({name: "/site-two_VAT"}), ["*_VAT"]));
    assert.ok(isHidden(model({id: "export-c.rvm~areas"}), ["*port-c*"]));
});

test("a model matching none of several patterns survives", () => {
    assert.equal(isHidden(model(), ["*port-b*", "*_VAT", "*port-c*"]), false);
});

test("a model with no description is still matched on what it has", () => {
    assert.ok(isHidden(model({description: null}), ["*site-one*"]));
});

test("both kinds of entry live in one list", () => {
    // The UI writes ids; a hand-edited pattern keeps applying to models that do
    // not exist yet, which a list of ticks cannot do. Neither disables the other.
    const hide = ["export-a.rvm~site-one", "*_VAT"];
    assert.ok(isHidden(model(), hide), "hidden by its exact id");
    assert.ok(isHidden(model({id: "x", name: "/site-two_VAT"}), hide), "hidden by the pattern");
    assert.equal(isHidden(model({id: "y", name: "/site-two", description: null}), hide), false);
});
