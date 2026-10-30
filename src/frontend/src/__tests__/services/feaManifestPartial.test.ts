import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {fetchFeaManifest} from "../../services/feaManifestPoll";
import type {ConvertResponse, FeaManifest} from "../../services/viewerApi";

// Mesh first: a running bake ships its mesh and a preliminary manifest, then
// fields as they land, and says so in its job status (``artefacts_ready``). The
// poll hands each preliminary manifest to ``onPartial`` and still resolves with
// the final one.

const manifest = (fields: string[], preliminary: boolean): FeaManifest =>
    ({
        version: 2,
        bake_version: 6,
        ...(preliminary ? {preliminary: true, pending_fields: []} : {}),
        src: "deck.sin",
        mesh: {url: "fea.mesh.glb", n_points: 4, n_cells: 1},
        fields: fields.map((name) => ({name_canonical: name})),
    }) as unknown as FeaManifest;

function response(body: unknown, preliminaryJob?: string): Response {
    const headers: Record<string, string> = {"Content-Type": "application/json"};
    if (preliminaryJob !== undefined) {
        headers["X-Fea-Manifest-State"] = "preliminary";
        headers["X-Fea-Bake-Job"] = preliminaryJob;
    }
    return new Response(JSON.stringify(body), {status: 200, headers});
}

const status = (over: Partial<ConvertResponse>): ConvertResponse =>
    ({
        job_id: "job-1",
        source_key: "deck.sin",
        derived_key: "",
        status: "running",
        progress: 0.5,
        stage: "baking",
        error: null,
        cached: false,
        ...over,
    }) as ConvertResponse;

const deps = {apiBase: "/api", scope: "shared", sourceKey: "deck.sin", pollMs: 1, timeoutMs: 5_000, sleep: async () => {}};

describe("fetchFeaManifest mesh first", () => {
    it("hands each preliminary manifest to onPartial as the job reports it, then resolves with the final", async () => {
        // The server: 202 first; then whatever storage holds when asked.
        let stored: {body: FeaManifest; prelim: boolean} | null = null;
        let first = true;
        const fetcher = async () => {
            if (first) {
                first = false;
                return new Response(JSON.stringify({job_id: "job-1"}), {status: 202});
            }
            return response(stored!.body, stored!.prelim ? "job-1" : undefined);
        };
        const steps: Array<[ConvertResponse, {body: FeaManifest; prelim: boolean} | null]> = [
            [status({stage: "parsing", progress: 0.1}), null],
            [status({progress: 0.2, artefacts_ready: {mesh_ready: true, fields_ready: 0, fields_total: 2}}), {body: manifest([], true), prelim: true}],
            // Same readiness again: no refetch.
            [status({progress: 0.25, artefacts_ready: {mesh_ready: true, fields_ready: 0, fields_total: 2}}), {body: manifest([], true), prelim: true}],
            [status({progress: 0.5, artefacts_ready: {mesh_ready: true, fields_ready: 1, fields_total: 2}}), {body: manifest(["U"], true), prelim: true}],
            [status({status: "done", progress: 1, artefacts_ready: {mesh_ready: true, fields_ready: 2, complete: true}}), {body: manifest(["U", "S"], false), prelim: false}],
        ];
        let i = 0;
        const convertStatus = async () => {
            const [st, s] = steps[Math.min(i++, steps.length - 1)];
            if (s) stored = s;
            return st;
        };
        const partials: string[][] = [];
        const final = await fetchFeaManifest({
            ...deps,
            fetcher,
            convertStatus,
            onPartial: (m, info) => {
                assert.equal(info.jobId, "job-1");
                assert.equal(m.preliminary, true);
                partials.push(m.fields.map((f) => f.name_canonical));
            },
        });
        assert.deepEqual(partials, [[], ["U"]]);
        assert.deepEqual(final.fields.map((f) => f.name_canonical), ["U", "S"]);
        assert.equal(final.preliminary, undefined);
    });

    it("opened while the bake runs: shows the preliminary manifest and follows the named job", async () => {
        let n = 0;
        const fetcher = async () => (n++ === 0 ? response(manifest([], true), "job-9") : response(manifest(["U"], false)));
        const polled: string[] = [];
        const convertStatus = async (jobId: string) => {
            polled.push(jobId);
            return status({job_id: jobId, status: "done", progress: 1});
        };
        const partials: number[] = [];
        const final = await fetchFeaManifest({
            ...deps,
            fetcher,
            convertStatus,
            onPartial: (m) => {
                partials.push(m.fields.length);
            },
        });
        assert.deepEqual(partials, [0]);
        assert.deepEqual(polled, ["job-9"]);
        assert.equal(final.fields.length, 1);
    });

    it("without onPartial a preliminary answer is just the answer (older callers)", async () => {
        const fetcher = async () => response(manifest([], true), "job-9");
        const convertStatus = async (): Promise<ConvertResponse> => {
            throw new Error("must not poll");
        };
        const m = await fetchFeaManifest({...deps, fetcher, convertStatus});
        assert.equal(m.preliminary, true);
    });

    it("a failing onPartial does not fail the load: the final manifest still comes", async () => {
        let first = true;
        const fetcher = async () => {
            if (first) {
                first = false;
                return new Response(JSON.stringify({job_id: "job-1"}), {status: 202});
            }
            return response(manifest([], true), "job-1");
        };
        let calls = 0;
        const convertStatus = async () =>
            calls++ === 0
                ? status({artefacts_ready: {mesh_ready: true, fields_ready: 0}})
                : status({status: "done", progress: 1});
        let finalFetch = false;
        const wrapped = async () => {
            const r = await fetcher();
            if (calls > 1) finalFetch = true;
            return calls > 1 ? response(manifest(["U"], false)) : r;
        };
        const m = await fetchFeaManifest({
            ...deps,
            fetcher: wrapped,
            convertStatus,
            onPartial: () => {
                throw new Error("viewer refused the partial");
            },
        });
        assert.ok(finalFetch);
        assert.equal(m.fields.length, 1);
    });
});
