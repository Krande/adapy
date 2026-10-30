import assert from "node:assert/strict";
import test from "node:test";

import {applyChangesOf, countsLine} from "@/components/admin/applyChangesItem";

test("an item without the block is a plain sweep item", () => {
    assert.equal(applyChangesOf({collection: "ALPHA"}), null);
    assert.equal(applyChangesOf(null), null);
});

test("counts, badges, cursors and per-role outcomes are read; absent parts stay empty", () => {
    const a = applyChangesOf({
        apply_changes: {
            since: "2026-01-01T00:00:00Z",
            next_cursor: "2026-01-02T00:00:00Z",
            nothing: false,
            counts: {changed: 3, inserted: 1, deleted: 0, unplaceable: 2},
            roles: {
                "geometry.db": {applied: true},
                "node-bounds-patch.ndjson.gz": {status: "refused", warning: "no baseline; run a full export first"},
            },
        },
    });
    assert.equal(a?.baseline, false);
    assert.equal(countsLine(a?.counts ?? null), "3 changed · 1 inserted · 2 unplaceable");
    assert.deepEqual(a?.roles, [
        {role: "geometry.db", applied: true, warning: null},
        {role: "node-bounds-patch.ndjson.gz", applied: false, warning: "no baseline; run a full export first"},
    ]);
    const bare = applyChangesOf({apply_changes: {baseline: true, counts: "garbage", roles: 7}});
    assert.deepEqual(bare, {counts: null, baseline: true, nothing: false, since: null, nextCursor: null, roles: []});
});

test("roles may also arrive as a list", () => {
    const a = applyChangesOf({apply_changes: {roles: [{role: "hierarchy-patch.json", refused: true, warning: "stale"}, {name: "x", status: "applied"}]}});
    assert.deepEqual(a?.roles.map((r) => [r.role, r.applied]), [["hierarchy-patch.json", false], ["x", true]]);
});

