// The mapping reaches the three.js controllers as their own settings.
import {test} from "node:test";
import assert from "node:assert/strict";
import {MOUSE} from "three";

import {orbitControlsButton} from "@/components/viewer/sceneHelpers/bindCameraMapping";

test("OrbitControls swaps rotate and pan itself under Shift / Ctrl, so the setting is pre-swapped", () => {
    assert.equal(orbitControlsButton("orbit", false), MOUSE.ROTATE);
    assert.equal(orbitControlsButton("orbit", true), MOUSE.PAN, "it will swap this back to rotate");
    assert.equal(orbitControlsButton("pan", true), MOUSE.ROTATE);
    assert.equal(orbitControlsButton("zoom", true), MOUSE.DOLLY);
    assert.equal(orbitControlsButton("none", false), -1);
});
