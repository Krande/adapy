import assert from "node:assert/strict";
import {test} from "node:test";

import {
    DIRECTIONS,
    directionFromDelta,
    markingItemsFor,
    type MarkingContext,
} from "../../shell/markingMenuItems";

// A marking menu is only faster than a list if the gesture is learnable, and that rests
// on two properties these tests pin: a given action always occupies the same direction,
// and an inapplicable action is disabled IN PLACE rather than removed. Break either and
// the menu becomes a radial list you have to read — slower than a normal one.

const ctx = (over: Partial<MarkingContext> = {}): MarkingContext => ({
    mode: "inspect",
    target: "geometry",
    hasSelection: true,
    hasEntities: true,
    feaActive: false,
    builderActive: false,
    ...over,
});

test("every context fills all eight directions exactly once", () => {
    for (const mode of ["inspect", "results", "build", "convert"] as const) {
        const items = markingItemsFor(ctx({mode}));
        const dirs = items.map((i) => i.dir);
        assert.equal(new Set(dirs).size, dirs.length, `${mode}: two items share a direction`);
        assert.equal(items.length, DIRECTIONS.length, `${mode}: expected a full wheel`);
    }
});

test("an action keeps its direction across every context", () => {
    // The core premise. If Hide moves because something else is unavailable, the muscle
    // memory is worthless.
    const dirOf = (c: MarkingContext, id: string) => markingItemsFor(c).find((i) => i.id === id)?.dir;
    const contexts = [
        ctx(),
        ctx({hasSelection: false}),
        ctx({hasEntities: false}),
        ctx({mode: "results", feaActive: true}),
        ctx({mode: "build", builderActive: true}),
        ctx({mode: "convert"}),
    ];
    for (const id of ["fit-all", "hide-selection", "unhide-all", "copy-names", "undo"]) {
        const dirs = new Set(contexts.map((c) => dirOf(c, id)));
        assert.equal(dirs.size, 1, `"${id}" appears at more than one direction: ${[...dirs].join(", ")}`);
    }
});

test("inapplicable actions are disabled, not removed", () => {
    const items = markingItemsFor(ctx({hasSelection: false, hasEntities: false}));
    const hide = items.find((i) => i.id === "hide-selection");
    assert.ok(hide, "Hide must still occupy its slot with nothing selected");
    assert.ok(hide!.disabledReason, "and must say why it is unavailable");
});

test("hide and unhide sit on opposite sides", () => {
    // They read as one axis, which is most of why the pair is memorable.
    const items = markingItemsFor(ctx());
    const hide = DIRECTIONS.indexOf(items.find((i) => i.id === "hide-selection")!.dir);
    const unhide = DIRECTIONS.indexOf(items.find((i) => i.id === "unhide-all")!.dir);
    assert.equal(Math.abs(hide - unhide), 1, "hide/unhide should be adjacent on the W side");
});

test("the mode slot swaps but stays in one place", () => {
    const slot = (c: MarkingContext) => markingItemsFor(c).find((i) => i.dir === "SE");
    assert.equal(slot(ctx({mode: "inspect"}))!.id, "section-planes");
    assert.equal(slot(ctx({mode: "results"}))!.id, "show-in-data");
    assert.equal(slot(ctx({mode: "build"}))!.id, "compile-preview");
});

test("a result action is disabled without a live session", () => {
    const item = markingItemsFor(ctx({mode: "results", feaActive: false})).find((i) => i.id === "show-in-data");
    assert.ok(item!.disabledReason);
    const live = markingItemsFor(ctx({mode: "results", feaActive: true})).find((i) => i.id === "show-in-data");
    assert.equal(live!.disabledReason, undefined);
});

test("a short drag picks nothing", () => {
    // The dead zone is what lets a plain right-click-and-release open the menu instead of
    // committing to whatever the cursor twitched towards.
    assert.equal(directionFromDelta(0, 0), null);
    assert.equal(directionFromDelta(8, 8), null);
    assert.notEqual(directionFromDelta(0, -60), null);
});

test("drag direction maps to the compass with screen coordinates", () => {
    // Screen y grows downward, so "up" is negative dy. Getting this inverted would put
    // every gesture 180° out.
    assert.equal(directionFromDelta(0, -60), "N");
    assert.equal(directionFromDelta(60, 0), "E");
    assert.equal(directionFromDelta(0, 60), "S");
    assert.equal(directionFromDelta(-60, 0), "W");
    assert.equal(directionFromDelta(50, -50), "NE");
    assert.equal(directionFromDelta(-50, 50), "SW");
});
