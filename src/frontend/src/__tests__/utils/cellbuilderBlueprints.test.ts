import assert from "node:assert/strict";
import {test} from "node:test";

import {resolveSelectedBlueprint} from "../../utils/cellbuilder/blueprints";

const list = [{slug: "steel_stru"}, {slug: "none"}];

test("keeps the current selection when the engine still offers it", () => {
    assert.equal(resolveSelectedBlueprint(list, "none"), "none");
});

test("falls back to the engine default (first) when current isn't offered", () => {
    // e.g. switching to an engine that doesn't advertise the old blueprint.
    assert.equal(resolveSelectedBlueprint(list, "pm_special"), "steel_stru");
});

test("null selection resolves to the engine default (first)", () => {
    assert.equal(resolveSelectedBlueprint(list, null), "steel_stru");
});

test("an engine advertising no blueprints yields null", () => {
    assert.equal(resolveSelectedBlueprint([], "steel_stru"), null);
    assert.equal(resolveSelectedBlueprint([], null), null);
});
