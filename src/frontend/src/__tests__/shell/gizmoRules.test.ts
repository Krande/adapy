import {test, describe} from "node:test";
import assert from "node:assert/strict";
import {gizmoReason} from "@/shell/gizmoRules";

const ctx = (selectionKind: string | null, modelOpen = true) => ({modelOpen, selectionKind});

describe("gizmo availability mirrors CellBuilderController's G/R/S bindings", () => {
    test("move works on a cell and on equipment", () => {
        assert.equal(gizmoReason("translate", ctx("cell")), null);
        assert.equal(gizmoReason("translate", ctx("equipment")), null);
    });

    test("rotate is equipment-only — a cell has no meaningful rotation", () => {
        assert.equal(gizmoReason("rotate", ctx("equipment")), null);
        assert.match(gizmoReason("rotate", ctx("cell")) ?? "", /equipment/);
    });

    test("resize is cell-only — equipment has no resize handles", () => {
        assert.equal(gizmoReason("resize", ctx("cell")), null);
        assert.match(gizmoReason("resize", ctx("equipment")) ?? "", /cell/);
    });

    test("no gizmo acts on a loft", () => {
        for (const g of ["translate", "rotate", "resize"] as const) {
            assert.match(gizmoReason(g, ctx("loft")) ?? "", /loft/, `${g} should decline a loft`);
        }
    });

    test("nothing selected and no model give distinct reasons", () => {
        assert.match(gizmoReason("translate", ctx(null)) ?? "", /selected/);
        assert.match(gizmoReason("translate", ctx(null, false)) ?? "", /model/);
    });

    test("the no-model reason wins over the no-selection one", () => {
        // Order matters: telling someone "nothing is selected" when there is nothing to
        // select from sends them looking for a selection they cannot make.
        assert.match(gizmoReason("rotate", ctx(null, false)) ?? "", /model/);
    });
});
