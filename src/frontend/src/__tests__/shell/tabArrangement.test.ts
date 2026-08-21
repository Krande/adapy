import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {ENTER_BODY_H, HEADER_H, LEAVE_BODY_H, shouldStackTabs} from "@/shell/tabArrangement";

const at = (heightPx: number, tabCount = 6, wasStacked = false) =>
    shouldStackTabs({tabCount, heightPx, wasStacked});

describe("tab arrangement", () => {
    test("a tall panel stacks its tabs as disclosures", () => {
        assert.equal(at(6 * HEADER_H + ENTER_BODY_H), true);
    });

    test("a short panel keeps the strip", () => {
        assert.equal(at(6 * HEADER_H + ENTER_BODY_H - 1), false);
    });

    test("the budget is headers plus ONE body, not one body each", () => {
        // The distinction from dockArrangement: a stacked tab becomes a collapsible, so
        // it costs a header row until opened. Charging it a full body would mean six
        // groups never stack on any real screen.
        const h = 6 * HEADER_H + ENTER_BODY_H;
        assert.equal(at(h), true);
        assert.ok(h < 6 * ENTER_BODY_H, "six full bodies would never fit");
    });

    test("two tabs never stack — a strip of two beats two headers", () => {
        assert.equal(at(5000, 2), false);
        assert.equal(at(5000, 3), true);
    });

    test("more tabs need more height", () => {
        const h = 4 * HEADER_H + ENTER_BODY_H;
        assert.equal(at(h, 4), true);
        assert.equal(at(h, 6), false);
    });

    test("hysteresis: the band between the thresholds holds its state", () => {
        assert.ok(LEAVE_BODY_H < ENTER_BODY_H);
        const inBand = 6 * HEADER_H + (ENTER_BODY_H + LEAVE_BODY_H) / 2;
        assert.equal(at(inBand, 6, true), true, "stacked should stay stacked");
        assert.equal(at(inBand, 6, false), false, "tabbed should stay tabbed");
    });
});
