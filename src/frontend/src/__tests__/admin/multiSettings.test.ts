import assert from "node:assert/strict";
import test from "node:test";

import {
    isMultiSetting,
    multiDefault,
    multiSettingError,
    multiValue,
    toggleChoice,
    withMultiDefaults,
} from "@/components/admin/scheduleSettings";

const targets = {
    name: "apply_targets",
    title: "Apply to",
    multiple: true,
    default: ["attributes", "geometry", "bounds", "relations"],
    choices: ["attributes", "geometry", "bounds", "relations"].map((v) => ({value: v, label: v.toUpperCase()})),
};
const single = {name: "mode", title: "Mode", choices: [{value: "a", label: "A"}]};

test("only an enum flagged multiple with choices is a multiple setting, whatever its name", () => {
    assert.equal(isMultiSetting(targets), true);
    assert.equal(isMultiSetting({...targets, name: "anything_else"}), true);
    assert.equal(isMultiSetting(single), false);
    assert.equal(isMultiSetting({...targets, choices: []}), false);
});

test("unset, a multiple setting holds its declared default; with none declared, every choice", () => {
    assert.deepEqual(multiValue(targets, undefined), targets.default);
    assert.deepEqual(multiDefault({...targets, default: undefined}), targets.default);
    assert.deepEqual(multiDefault({...targets, default: ["geometry", "nope"]}), ["geometry"]);
});

test("toggling keeps the choices' own order and never invents a value", () => {
    const off = toggleChoice(targets, undefined, "geometry", false);
    assert.deepEqual(off, ["attributes", "bounds", "relations"]);
    assert.deepEqual(toggleChoice(targets, off, "geometry", true), targets.default);
    assert.deepEqual(toggleChoice(targets, [], "bogus", true), []);
});

test("an untouched form sends the full list; ticking none is refused with a message", () => {
    assert.deepEqual(withMultiDefaults([targets, single], {mode: "a"}), {mode: "a", apply_targets: targets.default});
    assert.equal(multiSettingError([targets], {}), null);
    assert.match(multiSettingError([targets], {apply_targets: []}) ?? "", /Apply to/);
    assert.equal(multiSettingError([single], {}), null);
});
