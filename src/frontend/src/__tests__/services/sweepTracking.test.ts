import assert from "node:assert/strict";
import test from "node:test";

import type {ExportItem, ExportRun, SweepItem, SweepRun} from "@/services/viewerApi";

// `viewerApi` reaches `services/auth/oidc.ts`, which reads `sessionStorage` at module load, so the
// browser globals are stubbed BEFORE the dynamic import -- as in jobTracking.test.ts.
const memory = new Map<string, string>();
const storage = {
    getItem: (k: string): string | null => memory.get(k) ?? null,
    setItem: (k: string, v: string): void => void memory.set(k, String(v)),
    removeItem: (k: string): void => void memory.delete(k),
    clear: (): void => memory.clear(),
    key: (): string | null => null,
    get length(): number {
        return memory.size;
    },
};
const globals = globalThis as unknown as {sessionStorage: unknown; localStorage: unknown};
globals.sessionStorage = storage;
globals.localStorage = storage;

const {sweepToastState, exportToastState, sweepSkipTarget, exportSkipTarget, sweepActiveItems, runJobIds} = await import(
    "@/services/sweepTracking"
);
const {toastRunTarget} = await import("@/state/runFocusStore");

const item = (position: number, status: SweepItem["status"], extra: Partial<SweepItem> = {}): SweepItem => ({
    position,
    collection: `C${position}`,
    status,
    fetch_job_id: null,
    publish_job_id: null,
    revision: null,
    message: null,
    error: null,
    timings: {},
    started_at: null,
    finished_at: null,
    ...extra,
});

const run = (status: SweepRun["status"], items: SweepItem[], extra: Partial<SweepRun> = {}): SweepRun => ({
    id: "r1",
    scope: "shared",
    provider: "vendor",
    plugin_id: "vendor-plugin",
    job: "sweep",
    settings: {},
    schedule_id: null,
    requested_by: null,
    requested_via: "user",
    status,
    total: items.length,
    created_at: null,
    heartbeat_at: null,
    finished_at: null,
    error: null,
    items,
    ...extra,
});

test("a running sweep reports progress across collections and the current one's stage", () => {
    const s = sweepToastState(
        run("running", [
            item(0, "done"),
            item(1, "error"),
            item(2, "fetching", {stage: "site 3/10", progress: 0.5}),
            item(3, "pending"),
        ]),
    );
    assert.equal(s.status, "running");
    assert.equal(s.progress, 2.5 / 4);
    assert.equal(s.stage, "2/4 · 1 failed · C2: site 3/10");
});

test("a sweep running several collections at once lists each worker and counts every one's share", () => {
    const r = run("running", [
        item(0, "done"),
        item(1, "fetching", {stage: "export", progress: 0.5}),
        item(2, "publishing", {stage: null, progress: null}),
        item(3, "pending"),
    ]);
    assert.deepEqual(sweepActiveItems(r), [
        {position: 1, collection: "C1", stage: "export", progress: 0.5},
        {position: 2, collection: "C2", stage: "publishing", progress: null},
    ]);
    const s = sweepToastState(r);
    assert.equal(s.progress, 1.5 / 4);
    assert.match(s.stage, / · 2 running$/);
});

test("a sweep's job ids are its items' fetch and publish jobs: the toast's entries it owns", () => {
    const r = run("running", [
        item(0, "done", {fetch_job_id: "f0", publish_job_id: "p0"}),
        item(1, "fetching", {fetch_job_id: "f1"}),
        item(2, "pending"),
    ]);
    assert.deepEqual([...runJobIds(r)].sort(), ["f0", "f1", "p0"]);
});

test("collections ticked off are not counted as work left", () => {
    const s = sweepToastState(run("running", [item(0, "done"), item(1, "excluded"), item(2, "fetching"), item(3, "excluded")]));
    assert.equal(s.progress, 1 / 2);
    assert.match(s.stage, /^1\/2 /);
});

test("finished sweeps map onto the toast's terminal states", () => {
    assert.equal(sweepToastState(run("done", [item(0, "done")])).status, "done");
    const failed = sweepToastState(run("error", [item(0, "error")], {error: "1 of 1 collection(s) failed"}));
    assert.equal(failed.status, "error");
    assert.equal(failed.error, "1 of 1 collection(s) failed");
    const gone = sweepToastState(run("interrupted", [item(0, "fetching")]));
    assert.equal(gone.status, "cancelled");
    assert.match(gone.error ?? "", /stopped/);
});

test("a pending sweep skip shows, and blocks a second one", () => {
    const r = run("running", [item(0, "done"), item(1, "fetching", {stage: "export"})]);
    assert.equal(sweepSkipTarget(r), 1);
    const pending = {...r, skip_position: 1};
    assert.equal(sweepSkipTarget(pending), null);
    assert.match(sweepToastState(pending).stage, /skipping…$/);
    assert.equal(sweepSkipTarget(run("running", [item(0, "done"), item(1, "pending")])), null);
    assert.equal(sweepSkipTarget(run("done", [item(0, "fetching")])), null);
});

const exportItem = (position: number, status: ExportItem["status"], extra: Partial<ExportItem> = {}): ExportItem => ({
    position,
    provider: "vendor",
    collection: `C${position}`,
    hits: 3,
    status,
    job_id: null,
    message: null,
    error: null,
    timings: {},
    files: {written: 0, no_geometry: 0, failed: 0},
    file_errors: [],
    started_at: null,
    finished_at: null,
    ...extra,
});

const exportRun = (status: ExportRun["status"], items: ExportItem[], extra: Partial<ExportRun> = {}): ExportRun => ({
    id: "e1",
    scope: "shared",
    target_scope: "user:me",
    target_folder: "exports/x",
    status,
    total: items.length,
    hits: 12,
    query: null,
    options: {include_source_attributes: true, also: []},
    requested_by: null,
    created_at: null,
    heartbeat_at: null,
    finished_at: null,
    error: null,
    skip_position: null,
    files: {written: 5, no_geometry: 2, failed: 0},
    items,
    ...extra,
});

test("a running export reports items done, files written and how many run at once", () => {
    const s = exportToastState(
        exportRun("running", [
            exportItem(0, "done"),
            exportItem(1, "running", {progress: 0.5}),
            exportItem(2, "running", {progress: 0.5}),
            exportItem(3, "pending"),
        ]),
    );
    assert.equal(s.status, "running");
    assert.equal(s.progress, 2 / 4);
    assert.equal(s.stage, "1/4 · 5 written, 2 no geometry · 2 running");
    const one = exportToastState(exportRun("running", [exportItem(0, "partial"), exportItem(1, "running", {stage: "ifc 3/9"})]));
    assert.equal(one.stage, "1/2 · 5 written, 2 no geometry · C1: ifc 3/9");
    assert.equal(exportToastState(exportRun("done", [exportItem(0, "done")])).status, "done");
    assert.equal(exportToastState(exportRun("cancelled", [exportItem(0, "skipped")])).status, "cancelled");
});

test("skip current on an export means the lowest running position", () => {
    const r = exportRun("running", [exportItem(0, "done"), exportItem(3, "running"), exportItem(2, "running"), exportItem(4, "pending")]);
    assert.equal(exportSkipTarget(r), 2);
    assert.equal(exportSkipTarget({...r, skip_position: 2}), null);
    assert.equal(exportSkipTarget(exportRun("running", [exportItem(0, "pending")])), null);
    assert.equal(exportSkipTarget({...r, status: "done"}), null);
});

test("a toast entry's View run target: sweeps in Admin -> Providers, exports in the search panel", () => {
    assert.deepEqual(toastRunTarget("sweep:r1", () => null), {panel: "admin", tab: "providers", focus: {kind: "sweep", runId: "r1"}});
    assert.deepEqual(toastRunTarget("export:e1", (id) => (id === "e1" ? "project:p1" : null)), {
        panel: "search",
        tab: null,
        focus: {kind: "export", runId: "e1", scope: "project:p1"},
    });
    // An export whose scope is unknown, a job's key, an empty id: nothing to open.
    assert.equal(toastRunTarget("export:e2", () => null), null);
    assert.equal(toastRunTarget("models/a.ifc::glb", () => "shared"), null);
    assert.equal(toastRunTarget("sweep:", () => null), null);
});

test("a run item's own toast entry follows its job: the bounds extraction's site count, then the end", async () => {
    const {sweepItemToastState} = await import("@/services/sweepTracking");
    const measuring = sweepItemToastState(item(0, "fetching", {fetch_job_id: "f0", stage: "measuring site 12 of 82", progress: 0.17}));
    assert.deepEqual(measuring, {status: "running", progress: 0.17, stage: "measuring site 12 of 82", error: null});
    assert.equal(sweepItemToastState(item(0, "publishing", {stage: "ingesting"})).stage, "publishing: ingesting");
    assert.equal(sweepItemToastState(item(0, "pending")).status, "queued");
    assert.deepEqual(sweepItemToastState(item(0, "unchanged")), {status: "done", progress: 1, stage: "unchanged", error: null});
    const failed = sweepItemToastState(item(0, "error", {error: "boom", progress: 0.4}));
    assert.equal(failed.status, "error");
    assert.equal(failed.error, "boom");
});
