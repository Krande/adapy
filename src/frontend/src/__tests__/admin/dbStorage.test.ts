import assert from "node:assert/strict";
import test from "node:test";

import {
    ageRange,
    collectionKey,
    formatRows,
    parseDays,
    pruneSpecFor,
    refOfKey,
    retentionChanges,
    shareLabel,
    sharePercent,
    shareTargets,
} from "@/components/admin/database/dbStorage";
import {parseTabId} from "@/components/admin/adminTabs";

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
