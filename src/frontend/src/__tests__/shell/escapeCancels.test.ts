import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Escape cancels. Enter accepts.
//
// The cellbuilder had these the wrong way round for one case, and it was the case people
// hit most: mid-drag, Escape fell through to "put the gizmo away", which KEPT the
// half-finished drag. The one key every application uses to mean "forget it" was the key
// that committed your accident.
//
// A source-text test, because the behaviour lives in 3700 lines of imperative three.js
// wired to a real renderer, and the thing that breaks is an ORDER: a later branch of the
// Escape ladder catching the key before the cancel does. Order is exactly what reading
// the source can check and what a unit test of a pure helper could not, since the helper
// would not be the thing that regressed.

const SRC = path.resolve(
    import.meta.dirname,
    "../../components/viewer/CellBuilderController.tsx",
);

const read = () => fs.readFileSync(SRC, "utf8");

/** Index of the first match, or -1. Asserts the file is the one we think it is. */
function at(src: string, needle: string): number {
    return src.indexOf(needle);
}

describe("Escape cancels, Enter accepts", () => {
    const src = read();

    test("the controller still has an Escape ladder to check", () => {
        // Re-anchor rather than silently pass. regionCompat degraded into a test of
        // nothing exactly this way, by slicing on a string that had disappeared.
        assert.ok(src.length > 10_000, "CellBuilderController is not where this test expects");
        assert.ok(at(src, 'ev.key !== "Escape"') > 0, "the Escape guard is gone — re-anchor");
    });

    test("a widget drag is cancelled before anything else claims Escape", () => {
        const cancel = at(src, "cancelWidgetDrag()");
        assert.ok(cancel > 0, "cancelWidgetDrag is never called");

        // Everything below must come after it in the ladder, or it catches Escape first
        // and the drag is kept instead of reverted.
        for (const later of [
            "endModalMove(true)",
            "st.closePortMenu()",
            "st.stopPortGizmo()",
            "st.closeContextMenu()",
        ]) {
            const i = at(src, later);
            assert.ok(i > 0, `${later} is gone — re-anchor this test`);
            assert.ok(cancel < i, `${later} claims Escape before the drag is cancelled`);
        }
    });

    test("cancelling restores the pre-drag state rather than just hiding the gizmo", () => {
        // The bug was that Escape only did the second half. `undo()` is what makes it a
        // cancel: beginTransaction snapshotted the whole document at drag start.
        const body = src.slice(at(src, "const cancelWidgetDrag"), at(src, "const cancelWidgetDrag") + 600);
        assert.match(body, /st\.undo\(\)/, "cancelWidgetDrag does not restore anything");
        assert.match(body, /endTransaction\(\)/, "cancelWidgetDrag leaves the transaction open");
    });

    test("the port gizmo cancels too", () => {
        assert.ok(at(src, "cancelPortDrag()") > 0, "a port drag cannot be cancelled");
        const cancel = at(src, "cancelPortDrag()");
        assert.ok(cancel < at(src, "st.stopPortGizmo()"), "stopPortGizmo claims Escape first");
    });

    test("cancelling does not disable the widget mid-drag", () => {
        // TransformControls checks `enabled` in its pointerup handler as well, so
        // disabling mid-drag leaves `dragging` stuck true and the widget wedged on the
        // next click. The flag exists precisely to avoid that.
        const body = src.slice(at(src, "const cancelWidgetDrag"), at(src, "const cancelWidgetDrag") + 600);
        assert.ok(!/gizmo\.enabled\s*=\s*false/.test(body), "cancel disables the widget mid-drag");
        assert.match(src, /dragCancelled = true/, "the cancel flag is gone");
    });

    test("Enter keeps a modal move, Escape reverts it", () => {
        // The one place the two genuinely differ in outcome rather than in what they
        // dismiss. `false` = keep, `true` = revert.
        assert.ok(at(src, "endModalMove(false)") > 0, "Enter no longer accepts a modal move");
        assert.ok(at(src, "endModalMove(true)") > 0, "Escape no longer reverts a modal move");
    });

    test("Enter is not stolen from a field or from Shift+Enter", () => {
        // Compile-preview is Shift+Enter and belongs to the global handler; the HUD's
        // numeric inputs need a plain Enter to submit.
        assert.match(src, /ev\.key === "Enter" && !inField && !ev\.shiftKey/);
    });
});
