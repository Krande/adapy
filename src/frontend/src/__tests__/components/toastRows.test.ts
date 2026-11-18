// Which jobs the progress toast lists, and what its one scene-load row shows.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ConversionJob, ConvertStatus } from "@/state/conversionStore";
import { MODEL_LOAD_KEY, toastRows } from "@/components/conversion/conversionProgress/toastRows";

const job = (sourceKey: string, status: ConvertStatus, extra: Partial<ConversionJob> = {}): ConversionJob => ({
    sourceKey,
    jobId: `j-${sourceKey}`,
    derivedKey: `_derived/${sourceKey}.glb`,
    status,
    progress: 0,
    stage: "",
    error: status === "error" ? "boom" : null,
    startedAt: 0,
    ...extra,
});

const none = { loadCurrentName: null, currentClaimed: false };

test("running jobs are rows, keyed by their store key; failed ones are listed apart", () => {
    const rows = toastRows(
        {
            "a.ifc::glb": job("a.ifc", "running"),
            "b.ifc::glb": job("b.ifc", "error"),
            "c.ifc::glb": job("c.ifc", "done"),
        },
        none,
    );
    assert.deepEqual(rows.inProgress.map((j) => j.storeKey), ["a.ifc::glb"]);
    assert.deepEqual(rows.errored.map((j) => j.storeKey), ["b.ifc::glb"]);
    assert.equal(rows.loadName, null);
    assert.equal(rows.operationRunning, false);
});

test("the job feeding the current load and the model download show on the load row only", () => {
    const jobs = {
        "m.ifc::glb": job("m.ifc", "running"),
        [MODEL_LOAD_KEY]: job(MODEL_LOAD_KEY, "running"),
    };
    const rows = toastRows(jobs, { loadCurrentName: "m.ifc", currentClaimed: false });
    assert.deepEqual(rows.inProgress, []);
    assert.equal(rows.loadName, "m.ifc");
    assert.equal(rows.loadJob, jobs["m.ifc::glb"], "the conversion feeding the load, before the download");

    const direct = toastRows({ [MODEL_LOAD_KEY]: job(MODEL_LOAD_KEY, "running", { derivedKey: "x.glb" }) }, none);
    assert.equal(direct.loadName, "x.glb", "a load with no queue entry is named by the download");
    assert.equal(direct.loadJob?.derivedKey, "x.glb");

    assert.equal(toastRows(jobs, { loadCurrentName: "m.ifc", currentClaimed: true }).loadName, null, "a claimed load is told elsewhere");
});

test("an operation's steps stay listed while it runs, unless it is collapsed", () => {
    const jobs = {
        "op:1": job("op:1", "running"),
        "op:1#a": job("a", "done", { parent: "op:1" }),
        "op:1#b": job("b", "error", { parent: "op:1" }),
    };
    const rows = toastRows(jobs, none);
    assert.deepEqual(rows.inProgress.map((j) => j.storeKey), ["op:1", "op:1#a"], "a finished step stays; a failed one is an error row");
    assert.equal(rows.operationRunning, true);

    const collapsed = toastRows({ ...jobs, "op:1": job("op:1", "running", { collapsed: true }) }, none);
    assert.deepEqual(collapsed.inProgress.map((j) => j.storeKey), ["op:1"]);
});

test("claimed jobs are left out of both lists", () => {
    const rows = toastRows(
        { "a.ifc::glb": job("a.ifc", "running"), "b.ifc::glb": job("b.ifc", "error") },
        { ...none, isClaimed: () => true },
    );
    assert.deepEqual([rows.inProgress, rows.errored], [[], []]);
});
