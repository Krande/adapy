// Side by side on a viewport that keeps its models itself (the three.js-free one): the compiled model
// is moved along X past the cell builder's cells by the same offset the three.js viewport moves its
// group by, back on "off", and placed when it arrives if it was asked for before it was loaded.

import assert from "node:assert/strict";
import {test} from "node:test";

await import("../fixtures/panelRealm");
const {sideBySideOnViewport} = await import("@/utils/scene/handlers/side_by_side");
const {sideBySideOffsetX} = await import("@/utils/cellbuilder/sideBySide");
const {notifyModelLoaded} = await import("@/viewport/api/viewportApi");

type Box = [number, number, number, number, number, number];

function fakeViewport(loaded: boolean) {
    const shifts: number[][] = [];
    const state = {loaded};
    // The model spans x 2..12 in its own frame, placed at an offset of 100 along X.
    const box: Box = [102, 0, 0, 112, 5, 5];
    return {
        state,
        shifts,
        viewport: {
            shiftSource: (_name: string, shift: readonly [number, number, number]) => (state.loaded ? (shifts.push([...shift]), box) : null),
            sourceOrigin: () => [100, 0, 0] as const,
        },
    };
}

test("on: moved past the topology by the shared offset; off: back where it was placed", () => {
    const vp = fakeViewport(true);
    assert.equal(sideBySideOnViewport("procedural:m", true, 8, vp.viewport), true);
    assert.deepEqual(vp.shifts.at(-1), [sideBySideOffsetX(8, 2, Math.max(8, 10)), 0, 0]);
    sideBySideOnViewport("procedural:m", false, 8, vp.viewport);
    assert.deepEqual(vp.shifts.at(-1), [0, 0, 0]);
});

test("a model still on its way is placed when it arrives", () => {
    const vp = fakeViewport(false);
    sideBySideOnViewport("procedural:late", true, 8, vp.viewport);
    assert.equal(vp.shifts.length, 0);
    vp.state.loaded = true;
    notifyModelLoaded({model: "k", sourceName: "procedural:late", sceneExtras: ""});
    assert.deepEqual(vp.shifts.at(-1), [sideBySideOffsetX(8, 2, 10), 0, 0]);
});

test("a viewport that cannot move a model does nothing", () => {
    assert.equal(sideBySideOnViewport("x", true, 0, {sourceOrigin: () => null}), false);
});
