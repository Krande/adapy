import assert from "node:assert/strict";
import test from "node:test";

import {
    NO_FULL_EXPORT,
    applyChangesOf,
    countsLine,
    queuedByApplyRunOf,
    queuedFullExportOf,
    runWarningsOf,
    sweepCountsText,
} from "@/components/admin/applyChangesItem";
import {emptyChoiceLabel} from "@/components/admin/scheduleSettings";
import type {ScheduleSetting} from "@/services/api/assetSchedules";

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
        {role: "geometry.db", applied: true, status: "applied", warning: null},
        {role: "node-bounds-patch.ndjson.gz", applied: false, status: "refused", warning: "no baseline; run a full export first"},
    ]);
    const bare = applyChangesOf({apply_changes: {baseline: true, counts: "garbage", roles: 7}});
    assert.deepEqual(bare, {counts: null, baseline: true, nothing: false, since: null, nextCursor: null, roles: []});
});

test("roles may also arrive as a list", () => {
    const a = applyChangesOf({apply_changes: {roles: [{role: "hierarchy-patch.json", refused: true, warning: "stale"}, {name: "x", status: "applied"}]}});
    assert.deepEqual(a?.roles.map((r) => [r.role, r.applied]), [["hierarchy-patch.json", false], ["x", true]]);
});

test("roles beside the block on the item (where core puts them) are read, a failed one named", () => {
    const a = applyChangesOf({
        apply_changes: {nothing: false},
        roles: {"geometry.db": {status: "applied", subjects: 2},"attribute-index-patch.ndjson.gz": {status: "failed", error: "deadlock"}},
    });
    assert.deepEqual(a?.roles.map((r) => [r.role, r.status, r.warning]), [
        ["geometry.db", "applied", null],
        ["attribute-index-patch.ndjson.gz", "failed", "deadlock"],
    ]);
});

test("run-level warnings and the links between an apply-changes run and its queued full export", () => {
    const run = {
        warnings: ["BETA has not had a full project export yet", 7, ""],
        summary: {queued_full_exports: {run_id: "follow-1", collections: ["BETA", null]}},
    };
    assert.deepEqual(runWarningsOf(run), ["BETA has not had a full project export yet", "7"]);
    assert.deepEqual(queuedFullExportOf(run), {runId: "follow-1", collections: ["BETA"]});
    assert.equal(queuedByApplyRunOf(run), null);
    assert.equal(queuedByApplyRunOf({summary: {apply_changes_run_id: "run-1"}}), "run-1");
    assert.deepEqual(runWarningsOf({}), []);
    assert.equal(queuedFullExportOf({summary: {queued_full_exports: {collections: ["X"]}}}), null);
});

test("a collection left out for want of a full export counts as finished, and says so", () => {
    assert.equal(sweepCountsText({total: 3, counts: {done: 1, [NO_FULL_EXPORT]: 2}}), "3/3 · 2 without a full export");
});

test("core's own setting names its default on the empty choice; a provider's says the provider decides", () => {
    const choices = [
        {value: "warn", label: "Warn only"},
        {value: "export", label: "Queue a full project export"},
    ];
    const core = {name: "missing_full_export", type: "enum", title: "t", description: null, default: "warn", source: "enum", core: true, choices} as ScheduleSetting;
    assert.equal(emptyChoiceLabel(core, choices), "(default: Warn only)");
    assert.equal(emptyChoiceLabel({...core, core: false}, choices), "(provider default)");
});

test("a multi-target sweep item's per-target outcome, in the order they ran", async () => {
    const {sweepTargetsOf} = await import("@/components/admin/applyChangesItem");
    const item = {
        targets: {
            tree: {status: "error", error: "boom", timings: {queued_ms: 10, fetch_ms: 90}},
            geom: {status: "done", revision: "20260101T000000Z", timings: {fetch_ms: 50, publish_ms: 50}},
            bounds: {status: "skipped", message: "not run: tree did not publish in this run"},
        },
    };
    assert.deepEqual(sweepTargetsOf(item), [
        {target: "tree", status: "error", revision: null, note: "boom", totalMs: 100},
        {target: "geom", status: "done", revision: "20260101T000000Z", note: null, totalMs: 100},
        {target: "bounds", status: "skipped", revision: null, note: "not run: tree did not publish in this run", totalMs: null},
    ]);
    // Read from the timings too (a listing that does not lift it), and nothing for a one-job item.
    assert.equal(sweepTargetsOf({timings: {targets: {tree: {status: "done"}}}}).length, 1);
    assert.deepEqual(sweepTargetsOf({timings: {fetch_ms: 1}}), []);
});

test("an item's pre-build record reads back as one line, or why it was not attempted", async () => {
    const {prebuildOf, prebuildLine} = await import("@/components/admin/applyChangesItem");
    const p = prebuildOf({prebuild: {subjects: 6, cached: 2, built: 3, failed: 1, skipped: 0, ms: 1200, errors: ["s4: boom"]}});
    assert.equal(p && prebuildLine(p), "Meshes: 3 built · 2 cached · 1 failed of 6");
    assert.deepEqual(p?.errors, ["s4: boom"]);
    const not = prebuildOf({timings: {prebuild: {subjects: 0, reason: "geom did not publish"}}});
    assert.equal(not && prebuildLine(not), "Meshes not pre-built: geom did not publish");
    assert.equal(prebuildOf({timings: {}}), null);
});
