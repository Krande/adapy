// The camera input mapping: which button, modifier, wheel and touch gesture does what.
import {test} from "node:test";
import assert from "node:assert/strict";

import {
    CAMERA_PRESETS, CAMERA_PRESET_IDS, DEFAULT_CAMERA_MAPPING, cloneMapping, dragAction, dragModifier, mouseButtonOf,
    presetOf, sanitizeMapping, wheelZoomFactor,
} from "@/utils/camera/cameraMapping";

test("a drag is looked up by button and the one modifier that counts", () => {
    const m = DEFAULT_CAMERA_MAPPING;
    assert.equal(dragAction(m, "left"), "orbit");
    assert.equal(dragAction(m, "left", {shiftKey: true}), "pan");
    assert.equal(dragAction(m, "right"), "pan");
    assert.equal(dragAction(m, "middle", {altKey: true}), "zoom");
    assert.equal(dragModifier({shiftKey: true, ctrlKey: true, altKey: true}), "shift", "shift wins");
    assert.equal(dragModifier({metaKey: true, altKey: true}), "ctrl", "Cmd counts as Ctrl, and wins over Alt");
    assert.equal(mouseButtonOf(1), "middle");
    assert.equal(mouseButtonOf(4), null);
});

test("the wheel zooms in on a roll away, the other way when inverted, and not at all when switched off", () => {
    const m = cloneMapping(DEFAULT_CAMERA_MAPPING);
    assert.ok(wheelZoomFactor(m, -100)! < 1, "rolling away zooms in");
    m.wheel.inverted = true;
    assert.ok(wheelZoomFactor(m, -100)! > 1);
    m.wheel.zoom = false;
    assert.equal(wheelZoomFactor(m, -100), null);
});

test("every preset is recognised as itself, and an edited one as custom", () => {
    for (const id of CAMERA_PRESET_IDS) assert.equal(presetOf(cloneMapping(CAMERA_PRESETS[id].mapping)), id);
    const edited = cloneMapping(DEFAULT_CAMERA_MAPPING);
    edited.drag.left.alt = "zoom";
    assert.equal(presetOf(edited), "custom");
    assert.equal(DEFAULT_CAMERA_MAPPING.drag.left.alt, "none", "editing a copy leaves the preset alone");
});

test("stored settings are repaired field by field, never trusted", () => {
    const repaired = sanitizeMapping({
        drag: {left: {none: "pan", shift: "fly"}, middle: "nonsense"},
        wheel: {zoom: false, inverted: "yes"},
        touch: {one: "pan", two: "spin"},
        orbitSpeed: 99, zoomSpeed: "fast", damping: false,
    });
    assert.equal(repaired.drag.left.none, "pan", "a valid value is kept");
    assert.equal(repaired.drag.left.shift, DEFAULT_CAMERA_MAPPING.drag.left.shift, "an unknown action falls back");
    assert.deepEqual(repaired.drag.middle, DEFAULT_CAMERA_MAPPING.drag.middle);
    assert.deepEqual(repaired.wheel, {zoom: false, inverted: false, toCursor: false});
    assert.deepEqual(repaired.touch, {one: "pan", two: "zoom-pan"});
    assert.equal(repaired.orbitSpeed, 3, "clamped");
    assert.equal(repaired.zoomSpeed, 1);
    assert.equal(repaired.damping, false);
    assert.deepEqual(sanitizeMapping(null), DEFAULT_CAMERA_MAPPING);
});
