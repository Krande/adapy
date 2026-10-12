import assert from "node:assert/strict";
import test from "node:test";

import {choiceKey, choiceLabel, sameChoice, selectedKey, valueOfKey} from "@/components/admin/scheduleSettings";

const boolChoices = [
    {value: false, label: "Index only"},
    {value: true, label: "Index and geometry"},
];

test("a bool setting's picked option maps back to a real boolean", () => {
    assert.equal(valueOfKey(choiceKey(false), boolChoices), false);
    assert.equal(valueOfKey(choiceKey(true), boolChoices), true);
    assert.equal(valueOfKey("", boolChoices), null);
    assert.equal(valueOfKey('"false"', boolChoices), null);
});

test("stored bools select their option however they travelled", () => {
    for (const v of [false, "false", "False", " FALSE "]) assert.equal(selectedKey(v, boolChoices), "false");
    assert.equal(selectedKey(true, boolChoices), "true");
    assert.equal(selectedKey(null, boolChoices), "");
    assert.equal(selectedKey("maybe", boolChoices), "");
    // `false` is a value, not "unset"
    assert.notEqual(selectedKey(false, boolChoices), "");
});

test("non-bool choices keep comparing as text", () => {
    const choices = [
        {value: "tree", label: "Project tree"},
        {value: 3, label: "Three"},
    ];
    assert.equal(sameChoice("3", 3), true);
    assert.equal(sameChoice(0, false), false);
    assert.equal(valueOfKey(choiceKey(3), choices), 3);
    assert.equal(valueOfKey(choiceKey("tree"), choices), "tree");
    assert.equal(selectedKey("tree", choices), '"tree"');
});

test("summaries read the label, not the raw value", () => {
    assert.equal(choiceLabel(false, boolChoices), "Index only");
    assert.equal(choiceLabel("True", boolChoices), "Index and geometry");
    assert.equal(choiceLabel("x", undefined), "x");
});
