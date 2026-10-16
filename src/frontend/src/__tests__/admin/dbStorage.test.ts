import assert from "node:assert/strict";
import test from "node:test";

import {
    ageRange,
    collectionKey,
    duplicateSummary,
    foldBlocker,
    formatRows,
    groupPartitions,
    groupSelection,
    narrowingSkips,
    parseDays,
    pruneSpecFor,
    refOfKey,
    retentionChanges,
    shareLabel,
    sharePercent,
    shareTargets,
    toggleGroupSelection,
} from "@/components/admin/database/dbStorage";
import {parseTabId} from "@/components/admin/adminTabs";
import type {DbAttributePartition, DbDuplicateGroup} from "@/services/api/adminDb";

const part = (scope: string, provider: string, collection: string, size: number, extra: Partial<DbAttributePartition> = {}): DbAttributePartition => ({
    name: `attr_p_${provider}_${collection}_${scope.length}`,
    parent: "attr_nodes",
    scope,
    provider,
    collection,
    revision: "r1",
    rows: 10,
    rows_exact: true,
    indexed_rows: 10,
    ingested_at: null,
    total_bytes: size,
    table_bytes: size,
    index_bytes: 0,
    toast_bytes: 0,
    ...extra,
});

test("partitions group by scope and provider, largest group first, with the group's share targets", () => {
    const groups = groupPartitions(
        [
            part("projects/p1", "mesh-a", "beta", 10),
            part("shared", "mesh-a", "alpha", 50, {shared_with_group: ["projects/p1"]}),
            part("projects/p1", "mesh-a", "alpha", 30, {rows_exact: false}),
        ],
        [{scope: "projects/p1", provider: "mesh-a", target_scope: "users/u1", shared_at: null, shared_by: null}],
    );
    assert.deepEqual(groups.map((g) => [g.scope, g.partitions.map((p) => p.collection), g.totalBytes]), [
        ["shared", ["alpha"], 50],
        ["projects/p1", ["alpha", "beta"], 40],
    ]);
    assert.deepEqual(groups[0].sharedWith, ["projects/p1"]);
    assert.deepEqual(groups[1].sharedWith, ["users/u1"]);
    assert.equal(groups[1].rows, 20);
    assert.equal(groups[1].rowsExact, false);
});

test("a group's select-all ticks every collection, then unticks them", () => {
    const keys = ["a", "b"];
    assert.equal(groupSelection(keys, new Set()), "none");
    assert.equal(groupSelection(keys, new Set(["a"])), "some");
    const all = toggleGroupSelection(keys, new Set(["a", "x"]));
    assert.deepEqual([...all].sort(), ["a", "b", "x"]);
    assert.deepEqual([...toggleGroupSelection(keys, all)], ["x"]);
});

test("a duplicate group reads as one line, and only one with a Shared side folds", () => {
    const g: DbDuplicateGroup = {
        provider: "mesh-a",
        scopes: ["shared", "projects/p1"],
        keep_scope: "shared",
        drop_scope: "projects/p1",
        collections: 89,
        same_revision: 31,
        newer_ingest: {shared: 58, "projects/p1": 0},
        bytes: {shared: 42, "projects/p1": 41},
        rows: {shared: 1, "projects/p1": 1},
        reclaimable_bytes: 41,
    };
    const targets = [{scope: "shared", label: "Shared"}, {scope: "projects/p1", label: "plant-a"}];
    assert.equal(
        duplicateSummary(g, targets, (n) => `${n} B`),
        "mesh-a: 89 collections held by both plant-a and Shared · 31 at the same revision · 41 B reclaimable",
    );
    assert.equal(foldBlocker(g), null);
    const neither = {...g, scopes: ["projects/p1", "projects/p2"] as [string, string], keep_scope: null, drop_scope: null, reclaimable_bytes: null};
    assert.match(foldBlocker(neither) ?? "", /Neither copy is in Shared/);
    assert.doesNotMatch(duplicateSummary(neither, targets, String), /reclaimable/);
});

test("copies left alone because the newest is narrower are listed apart", () => {
    const copy = (scope: string, extra = {}) => ({scope, revision: "r1", rows: 1, bytes: 7, ingested_at: null, ...extra});
    const skips = narrowingSkips({
        collections: [
            {provider: "mesh-a", collection: "alpha", kept: copy("shared"), dropped: [copy("projects/p1", {action: "fold" as const})], skipped: []},
            {provider: "mesh-a", collection: "beta", kept: copy("projects/p1"), dropped: [], skipped: [copy("shared", {reason: "narrowing"})]},
        ],
    });
    assert.deepEqual(skips, [{provider: "mesh-a", collection: "beta", scope: "shared", keptScope: "projects/p1", bytes: 7}]);
});

test("days are whole numbers of at least one", () => {
    assert.equal(parseDays(" 30 "), 30);
    assert.equal(parseDays(""), null);
    for (const bad of ["0", "-3", "1.5", "soon", "1e3"]) assert.equal(parseDays(bad), "invalid", bad);
});

test("a collection key round-trips whatever the names contain", () => {
    const ref = {scope: "projects/abc", provider: "p", collection: 'we"ird/ALPHA'};
    assert.deepEqual(refOfKey(collectionKey(ref)), ref);
});

test("an age area's form becomes older_than_days, or says what is missing", () => {
    const sweeps = {id: "sweeps", prune: "age" as const};
    assert.deepEqual(pruneSpecFor(sweeps, {days: "30", collections: new Set()}), {spec: {older_than_days: 30}});
    assert.ok("error" in pruneSpecFor(sweeps, {days: "", collections: new Set()}));
    assert.ok("error" in pruneSpecFor(sweeps, {days: "0", collections: new Set()}));
    assert.ok("error" in pruneSpecFor({id: "other", prune: null}, {days: "30", collections: new Set()}));
});

test("ticked attribute collections win over a day count", () => {
    const attrs = {id: "attribute_index", prune: "collections" as const};
    const ref = {scope: "shared", provider: "p", collection: "alpha"};
    assert.deepEqual(pruneSpecFor(attrs, {days: "90", collections: new Set([collectionKey(ref)])}), {
        spec: {collections: [ref]},
    });
    assert.deepEqual(pruneSpecFor(attrs, {days: "90", collections: new Set()}), {spec: {older_than_days: 90}});
    assert.ok("error" in pruneSpecFor(attrs, {days: "", collections: new Set()}));
});

test("retention edits diff into sets, clears and invalid fields", () => {
    const {changes, invalid} = retentionChanges(
        {sweeps: 30, audit_log: 90},
        {sweeps: "30", audit_log: "", exports: "14", metrics: "x", change_checks: ""},
    );
    assert.deepEqual(changes, {audit_log: null, exports: 14});
    assert.deepEqual(invalid, ["metrics"]);
});

test("numbers read as estimates or exact counts", () => {
    assert.equal(formatRows(1_268_101, false), "~1.3M");
    assert.equal(formatRows(42, true), "42");
    assert.equal(formatRows(25_000, true), "25k");
    assert.equal(sharePercent(1, 4), "25.0");
    assert.equal(sharePercent(1, 0), "0.0");
    assert.equal(ageRange("2026-09-13T10:00:00Z", "2026-10-12T11:00:00Z"), "2026-09-13 → 2026-10-12");
    assert.equal(ageRange("2026-10-12T01:00:00Z", "2026-10-12T11:00:00Z"), "2026-10-12");
    assert.equal(ageRange(null, null), "—");
});

test("a partition is offered to Shared, the admin's own scope and live projects, never its own", () => {
    const projects = [
        {id: "p2", name: "beta", archived_at: null},
        {id: "p1", name: "alpha"},
        {id: "p3", name: "gone", archived_at: "2026-01-01T00:00:00Z"},
    ];
    const all = shareTargets("sub-1", projects, null);
    assert.deepEqual(all.map((t) => t.scope), ["shared", "users/sub-1", "projects/p1", "projects/p2"]);
    assert.deepEqual(shareTargets("sub-1", projects, "projects/p1").map((t) => t.scope), ["shared", "users/sub-1", "projects/p2"]);
    assert.deepEqual(shareTargets(null, [], null), [{scope: "shared", label: "Shared"}]);
    assert.equal(shareLabel("projects/p2", all), "beta");
    assert.equal(shareLabel("users/someone-else", all), "users/someone-else");
});

test("the Database tab is a routable admin tab", () => {
    assert.deepEqual(parseTabId("database", new Set()), {tab: "database"});
});
