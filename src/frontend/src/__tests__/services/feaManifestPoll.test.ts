import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {ApiError, fetchFeaManifest} from "../../services/feaManifestPoll";
import type {ConvertResponse, FeaManifest} from "../../services/viewerApi";

// Minimal manifest shape used as the "happy path" body. The picker
// reads more keys than this, but the orchestration layer doesn't
// care — it just parses and returns. Picker-contract checks live in
// the backend test suite.
const FAKE_MANIFEST: FeaManifest = {
    version: 1,
    src: "models/wall.rmed",
    mesh: {url: "fea.mesh.glb", n_points: 10, n_cells: 5},
    fields: [
        {
            name_canonical: "DEPL",
            name_native: "DEPL",
            kind: "vector3",
            category: "displacement",
            support: "nodal",
            analysis_kind: "static",
            components: ["DX", "DY", "DZ"],
            blob: {
                url: "fea.DEPL.bin",
                header_bytes: 1024,
                stride_bytes: 120,
                dtype: "float32",
                byte_order: "little",
            },
            n_steps: 1,
            steps: [{i: 0, value: 0, label: "DEPL"}],
            scalar_range: {DX: [0, 1], DY: [0, 1], DZ: [0, 1], magnitude: [0, 1]},
            default_view: {reduction: "magnitude", colormap: "viridis"},
        },
    ],
};

function jsonResponse(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: {"Content-Type": "application/json"},
    });
}

function makeStatus(
    overrides: Partial<ConvertResponse> = {},
): ConvertResponse {
    return {
        job_id: "job-1",
        source_key: "models/wall.rmed",
        derived_key: "_derived/models/wall.rmed.fea/fea.manifest.json",
        target_format: "glb",
        status: "running",
        progress: 0.5,
        stage: "baking",
        error: null,
        cached: false,
        ...overrides,
    } as ConvertResponse;
}

function baseDeps() {
    return {
        apiBase: "/api",
        scope: "user:me",
        sourceKey: "models/wall.rmed",
        pollMs: 1,
        timeoutMs: 5_000,
        sleep: async () => {},
    };
}

describe("fetchFeaManifest", () => {
    it("returns the body on 200 (cache hit)", async () => {
        const fetcher = async () => jsonResponse(200, FAKE_MANIFEST);
        const convertStatus = async () => {
            throw new Error("convertStatus must not be called on cache hit");
        };
        const m = await fetchFeaManifest({
            ...baseDeps(),
            fetcher,
            convertStatus,
        });
        assert.equal(m.src, "models/wall.rmed");
        assert.equal(m.fields.length, 1);
        assert.equal(m.fields[0].name_canonical, "DEPL");
    });

    it("does NOT mistake a 202 for a manifest body (regression)", async () => {
        // Regression for the bug where `Response.ok` short-circuited
        // the cache-hit path even on 202, returning the queued-job
        // payload as if it were a manifest. The picker then read
        // .fields.length on undefined and crashed.
        const calls: string[] = [];
        const fetcher = async () => {
            calls.push("fetch");
            if (calls.length === 1) {
                return jsonResponse(202, {job_id: "job-1", stage: "queued", progress: 0});
            }
            return jsonResponse(200, FAKE_MANIFEST);
        };
        const convertStatus = async () => makeStatus({status: "done", progress: 1});
        const m = await fetchFeaManifest({
            ...baseDeps(),
            fetcher,
            convertStatus,
        });
        // The shape must be the manifest, not the queued-job stub.
        assert.equal(m.src, "models/wall.rmed");
        assert.ok(Array.isArray(m.fields));
        assert.equal(m.fields.length, 1);
        // Two fetches: the initial 202 and the post-poll re-fetch.
        assert.equal(calls.length, 2);
    });

    it("polls until the job hits status=done", async () => {
        let fetchCount = 0;
        const fetcher = async () => {
            fetchCount++;
            // First fetcher call = initial endpoint hit → 202.
            // Subsequent calls = post-poll re-fetch → 200 with body.
            return fetchCount === 1
                ? jsonResponse(202, {job_id: "job-1"})
                : jsonResponse(200, FAKE_MANIFEST);
        };
        const statusCalls: ConvertResponse[] = [
            makeStatus({status: "queued", stage: "queued", progress: 0}),
            makeStatus({status: "running", stage: "parsing", progress: 0.3}),
            makeStatus({status: "running", stage: "uploading", progress: 0.9}),
            makeStatus({status: "done", stage: "ready", progress: 1}),
        ];
        const convertStatus = async () => statusCalls.shift()!;
        const stages: string[] = [];
        const m = await fetchFeaManifest({
            ...baseDeps(),
            fetcher,
            convertStatus,
            onProgress: ({stage}) => stages.push(stage),
        });
        assert.equal(m.fields.length, 1);
        // Initial "queued" from the 202 payload (queued.stage falls
        // back to "queued" since the 202 body omits stage), then a
        // change for each new stage during the poll. The first poll
        // returns the same "queued" stage so doesn't re-fire.
        assert.deepEqual(stages, ["queued", "parsing", "uploading", "ready"]);
    });

    it("throws when the job hits status=error", async () => {
        const fetcher = async () => jsonResponse(202, {job_id: "job-1"});
        const convertStatus = async () =>
            makeStatus({status: "error", error: "rmed parse blew up", stage: "convert"});
        await assert.rejects(
            () =>
                fetchFeaManifest({
                    ...baseDeps(),
                    fetcher,
                    convertStatus,
                }),
            (err: unknown) =>
                err instanceof ApiError &&
                err.status === 500 &&
                err.message.includes("rmed parse blew up"),
        );
    });

    it("throws AbortError when convertStatus returns cancelled", async () => {
        // Server-side kill flips the audit row to ``cancelled`` —
        // the poll loop must terminate the same way as an explicit
        // signal.abort() so call sites don't surface an error toast.
        const fetcher = async () => jsonResponse(202, {job_id: "job-1"});
        const convertStatus = async () => makeStatus({status: "cancelled"});
        await assert.rejects(
            () =>
                fetchFeaManifest({
                    ...baseDeps(),
                    fetcher,
                    convertStatus,
                }),
            (err: unknown) =>
                err instanceof DOMException && err.name === "AbortError",
        );
    });

    it("throws on unexpected non-200/202 status (e.g. 503 no NATS)", async () => {
        const fetcher = async () =>
            new Response("bake disabled (no NATS configured)", {status: 503});
        const convertStatus = async () => {
            throw new Error("must not be called");
        };
        await assert.rejects(
            () =>
                fetchFeaManifest({
                    ...baseDeps(),
                    fetcher,
                    convertStatus,
                }),
            (err: unknown) => err instanceof ApiError && err.status === 503,
        );
    });

    it("times out when the job never finishes", async () => {
        const fetcher = async () => jsonResponse(202, {job_id: "job-1"});
        const convertStatus = async () =>
            makeStatus({status: "running", progress: 0.5});
        // Virtual clock: every now() call advances by half the
        // timeout, so the second poll exceeds the cap.
        let virtualNow = 0;
        const m = fetchFeaManifest({
            ...baseDeps(),
            fetcher,
            convertStatus,
            timeoutMs: 100,
            now: () => {
                const t = virtualNow;
                virtualNow += 80;
                return t;
            },
        });
        await assert.rejects(
            () => m,
            (err: unknown) => err instanceof ApiError && err.status === 504,
        );
    });
});
