import assert from "node:assert/strict";
import test from "node:test";

import type {ConversionJob} from "@/state/conversionStore";

// `jobTracking` reaches `services/auth/oidc.ts`, which reads `sessionStorage` at module load, so the
// browser globals are stubbed BEFORE the dynamic import -- as in sweepTracking.test.ts.
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

const {beginOperation, operationProgress, MODEL_LOAD_KEY} = await import("@/services/operationTracking");
const {useConversionStore} = await import("@/state/conversionStore");

const jobs = () => useConversionStore.getState().jobs;

/** A step as `op.trackJob` would leave it, without the poll behind it. */
function step(parent: string, id: string, patch: Partial<ConversionJob> = {}): void {
    useConversionStore.getState().setJob(`${parent}#${id}`, {
        sourceKey: id,
        jobId: id,
        derivedKey: "",
        status: "running",
        progress: 0,
        stage: "",
        error: null,
        startedAt: 0,
        parent,
        ...patch,
    });
}

test("progress is the mean of the steps, a finished step whole; the stage is the running step's", () => {
    const s = (status: ConversionJob["status"], progress: number, sourceKey = "x", stage = "") =>
        ({sourceKey, jobId: "", derivedKey: "", status, progress, stage, error: null, startedAt: 0}) as ConversionJob;
    assert.deepEqual(operationProgress([]), {progress: 0, stage: null});
    const r = operationProgress([s("done", 0.2), s("running", 0.5, "build", "meshing")]);
    assert.equal(r.progress, 0.75);
    assert.equal(r.stage, "build: meshing");
});

test("an operation stays up across a gap between steps and ends, with its rows, at the last release", () => {
    const op = beginOperation("Request and load");
    const release = op.hold();
    step(op.key, "fetch");
    step(op.key, "fetch", {status: "done", progress: 1});
    // Between two jobs: nothing running, and the operation is still there, still running.
    assert.equal(jobs()[op.key]?.status, "running");
    step(op.key, "build", {progress: 0.5, stage: "meshing"});
    assert.equal(jobs()[op.key]?.progress, 0.75);
    assert.equal(jobs()[op.key]?.stage, "build: meshing");
    const loadHold = op.hold();
    step(op.key, "build", {status: "done", progress: 1});
    release();
    assert.ok(jobs()[op.key], "a load still holds it");
    loadHold();
    assert.equal(jobs()[op.key], undefined);
    assert.equal(jobs()[`${op.key}#fetch`], undefined);
    assert.equal(jobs()[`${op.key}#build`], undefined);
});

test("a failed step ends it as an error and stays reachable; its own progress and stage win", () => {
    const op = beginOperation("Restore", {collapsed: true});
    const release = op.hold();
    assert.equal(jobs()[op.key]?.collapsed, true);
    op.progress(0.5);
    op.stage("2 of 4: deck");
    step(op.key, "b1", {progress: 0.1});
    assert.equal(jobs()[op.key]?.progress, 0.5);
    assert.equal(jobs()[op.key]?.stage, "2 of 4: deck");
    // The scene's model download is added to the stage while it runs.
    useConversionStore.getState().setJob(MODEL_LOAD_KEY, {
        sourceKey: MODEL_LOAD_KEY, jobId: "load", derivedKey: "deck", status: "running",
        progress: 0.3, stage: "Downloading deck", error: null, startedAt: 0,
    });
    assert.equal(jobs()[op.key]?.stage, "2 of 4: deck · Downloading deck");
    useConversionStore.getState().clearJob(MODEL_LOAD_KEY);
    step(op.key, "b1", {status: "error", error: "boom"});
    release();
    assert.equal(jobs()[op.key]?.status, "error");
    assert.equal(jobs()[op.key]?.error, "b1: boom");
    assert.ok(jobs()[`${op.key}#b1`], "the failed step stays");
    useConversionStore.getState().clearJob(op.key);
    useConversionStore.getState().clearJob(`${op.key}#b1`);
});

test("dismissing the operation takes its rows with it", () => {
    const op = beginOperation("Load set");
    const release = op.hold();
    step(op.key, "a");
    useConversionStore.getState().clearJob(op.key);
    assert.equal(jobs()[`${op.key}#a`], undefined);
    release(); // after a dismissal: nothing comes back
    assert.equal(jobs()[op.key], undefined);
});
