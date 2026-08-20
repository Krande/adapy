import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {ENTER_STACK_H, LEAVE_STACK_H, shouldStack} from "@/shell/dockArrangement";

const at = (heightPx: number, over: Partial<Parameters<typeof shouldStack>[0]> = {}) =>
    shouldStack({dock: "right", panelCount: 2, heightPx, wasStacked: false, ...over});

describe("dock arrangement", () => {
    test("a tall dock stacks its panels", () => {
        assert.equal(at(ENTER_STACK_H * 2), true);
    });

    test("a short dock keeps tabs", () => {
        assert.equal(at(ENTER_STACK_H * 2 - 1), false);
    });

    test("the threshold scales with the number of panels", () => {
        // Three panels in the height two would fit is three cramped panels, not a win.
        const h = ENTER_STACK_H * 2;
        assert.equal(at(h, {panelCount: 2}), true);
        assert.equal(at(h, {panelCount: 3}), false);
    });

    test("a single panel never stacks", () => {
        assert.equal(at(5000, {panelCount: 1}), false, "a header for the only thing there");
    });

    test("the bottom dock never stacks", () => {
        // Wide-and-short by design: the FEA table, the conversion log. Stacking leaves
        // every one of them too short to read.
        assert.equal(at(5000, {dock: "bottom"}), false);
    });

    test("hysteresis: leaving needs a smaller height than entering", () => {
        assert.ok(LEAVE_STACK_H < ENTER_STACK_H);

        // In the band between the two thresholds the arrangement must HOLD, whichever
        // way it came in. Without this, dragging a splitter across the boundary flips the
        // layout every frame, which reads as a fault rather than a feature.
        const inBand = ((ENTER_STACK_H + LEAVE_STACK_H) / 2) * 2;
        assert.equal(at(inBand, {wasStacked: true}), true, "stacked should stay stacked");
        assert.equal(at(inBand, {wasStacked: false}), false, "tabbed should stay tabbed");
    });

    test("below the leave threshold it unstacks even if it was stacked", () => {
        assert.equal(at(LEAVE_STACK_H * 2 - 1, {wasStacked: true}), false);
    });
});
