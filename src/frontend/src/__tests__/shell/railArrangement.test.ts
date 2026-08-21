import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {arrangeRail, canHide, customisableTools} from "@/shell/railArrangement";

import type {RailItem} from "@/shell/railArrangement";

const t = (id: string, extra: Partial<RailItem> = {}): RailItem => ({id, ...extra});
const d = (id: string): RailItem => ({id, divider: true});

// files │ fit · focus │ hide · show-all │ undo — the shape of the real rail.
const RAIL = [
    t("files", {essential: true}),
    d("d0"),
    t("fit"),
    t("focus"),
    d("d1"),
    t("hide"),
    t("unhide"),
    d("d2"),
    t("undo"),
];

const ids = (hidden: string[] = []) => arrangeRail(RAIL, hidden).map((x) => x.id);

describe("arrangeRail", () => {
    test("nothing hidden is the rail as written", () => {
        assert.deepEqual(ids(), RAIL.map((x) => x.id));
    });

    test("hiding a tool leaves its group's rule alone", () => {
        assert.deepEqual(ids(["fit"]), ["files", "d0", "focus", "d1", "hide", "unhide", "d2", "undo"]);
    });

    test("emptying a group takes its rule with it", () => {
        // Two rules in a row is not a group boundary, it is a rendering fault — which is
        // what it looks like to anyone who did not hide the tools between them.
        assert.deepEqual(ids(["fit", "focus"]), ["files", "d0", "hide", "unhide", "d2", "undo"]);
    });

    test("a rule never lands against the top edge", () => {
        // Hiding the first tool would otherwise open the rail with a horizontal line
        // floating above everything.
        const out = arrangeRail(RAIL, ["files"]);
        assert.ok(!out[0].divider, `rail starts with a divider: ${out.map((x) => x.id)}`);
        assert.deepEqual(out.map((x) => x.id), ["fit", "focus", "d1", "hide", "unhide", "d2", "undo"]);
    });

    test("a rule never lands against the bottom edge", () => {
        const out = arrangeRail(RAIL, ["undo"]);
        assert.ok(!out[out.length - 1].divider, `rail ends with a divider: ${out.map((x) => x.id)}`);
    });

    test("hiding everything hideable leaves no stray rules", () => {
        const out = arrangeRail(RAIL, ["fit", "focus", "hide", "unhide", "undo"]);
        assert.deepEqual(out.map((x) => x.id), ["files"]);
    });

    test("an unknown hidden id is ignored, not an error", () => {
        // A tool removed in a later release is still named in someone's saved prefs.
        assert.deepEqual(ids(["a-tool-that-left"]), RAIL.map((x) => x.id));
    });

    test("a tool absent from the hidden list shows — new tools are not hidden by old prefs", () => {
        // The reason prefs store the HIDDEN set rather than the visible one: a tool added
        // later is in nobody's saved list, and a visible-set would make it invisible to
        // every existing user with no error to explain it.
        const withNew = [...RAIL, t("measure")];
        assert.ok(arrangeRail(withNew, ["fit"]).some((x) => x.id === "measure"));
    });
});

describe("customisableTools", () => {
    test("dividers are not choices, and essentials are not offered", () => {
        assert.deepEqual(customisableTools(RAIL).map((x) => x.id), [
            "fit",
            "focus",
            "hide",
            "unhide",
            "undo",
        ]);
    });
});

describe("canHide", () => {
    test("the last visible tool cannot be hidden", () => {
        // An empty rail is indistinguishable from a broken one, and the control that
        // puts it back lives in a menu you now have no reason to believe exists.
        const allButOne = ["fit", "focus", "hide", "unhide"];
        assert.equal(canHide(RAIL, allButOne, "undo"), false);
    });

    test("unhiding is always allowed", () => {
        const allHidden = ["fit", "focus", "hide", "unhide", "undo"];
        assert.equal(canHide(RAIL, allHidden, "fit"), true);
    });

    test("hiding is allowed while more than one remains", () => {
        assert.equal(canHide(RAIL, ["fit"], "focus"), true);
    });

    test("the essential tool does not count towards the survivors", () => {
        // Storage is always shown, so it must not be what keeps the last hideable tool
        // hideable — otherwise the rail can be reduced to one un-hideable icon and the
        // customise dialog looks like it stopped working.
        assert.equal(canHide(RAIL, ["fit", "focus", "hide", "unhide"], "undo"), false);
    });
});
