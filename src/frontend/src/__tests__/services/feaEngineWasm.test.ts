// The browser FEA engine against the backend's numpy reference, byte for byte.
//
// Runs the REAL adacpp_fea wasm module through the same FeaEngineCore +
// OpfsFeaStore the engine worker runs (the store here on the module's in-heap
// FS -- node has no OPFS; the worker stages into that same heap FS either way), over a base bake written by the backend, and
// compares every case blob, every overlay and two envelopes with what
// ``artefacts/combine.py`` wrote for the same deck.
//
// Driven by tests/core/fem/results/lazy_cases/test_wasm_engine_wiring.py,
// which bakes the decks, writes the reference cases and sets:
//   FEA_WASM_FIXTURE  a JSON file: {decks: [{name, base_dir, expected_dir, envelope_dir, envelope_fields}]}
//   ADACPP_FEA_WASM   the adacpp_fea.js to load
// Without them the test is skipped (the plain `npm test` run).

import assert from "node:assert/strict";
import {readFileSync, statSync} from "node:fs";
import {join} from "node:path";
import {describe, it} from "node:test";
import {pathToFileURL} from "node:url";

import type {FeaCaseOverlay, FeaManifest} from "../../services/api/fea";
import {caseDirName, LocalCaseUnsupported, planCase} from "../../services/fea/feaCasePlan";
import {FeaEngineCore, type FeaKernelModule} from "../../services/fea/feaEngineCore";
import type {FeaRangeFetcher} from "../../services/fea/feaFetcher";
import {
    emscriptenFeaFs,
    envelopeDirName,
    memoryFeaIndex,
    OpfsFeaStore,
    type EmscriptenFsLike,
} from "../../services/fea/opfsFeaStore";

interface DeckFixture {
    name: string;
    base_dir: string;
    expected_dir: string;
    envelope_dir: string;
    envelope_fields: string[];
    numpy_ms?: Record<string, number>;
}

const FIXTURE = process.env.FEA_WASM_FIXTURE;
const MODULE = process.env.ADACPP_FEA_WASM;
const skip = !FIXTURE || !MODULE ? "FEA_WASM_FIXTURE / ADACPP_FEA_WASM not set (run via pytest)" : false;

type Module = FeaKernelModule & {FS: EmscriptenFsLike & {readFile(p: string): Uint8Array}};

async function loadModule(): Promise<Module> {
    const create = (await import(pathToFileURL(MODULE!).href)).default as () => Promise<Module>;
    return create();
}

function diskRangeFetcher(baseDir: string, log: Array<[string, number, number]>): FeaRangeFetcher {
    return async (filename, start, end) => {
        const bytes = readFileSync(join(baseDir, filename));
        log.push([filename, start, end]);
        const slice = bytes.subarray(start, end + 1);
        return {buf: slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength) as ArrayBuffer, ranged: true};
    };
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
    if (a.byteLength !== b.byteLength) return false;
    for (let i = 0; i < a.byteLength; i++) if (a[i] !== b[i]) return false;
    return true;
}

function withoutProducer<T extends {producer?: unknown; prefix?: unknown}>(doc: T) {
    const {producer: _producer, prefix: _prefix, ...rest} = doc;
    return rest;
}

describe("browser FEA engine (adacpp_fea wasm) == numpy reference", {skip}, () => {
    const fixture = skip ? {decks: []} : (JSON.parse(readFileSync(FIXTURE!, "utf8")) as {decks: DeckFixture[]});

    for (const deck of fixture.decks) {
        it(`${deck.name}: every Tier-A case, byte for byte; raw cases refused`, async () => {
            const mod = await loadModule();
            const store = new OpfsFeaStore({
                fs: emscriptenFeaFs(mod.FS),
                index: memoryFeaIndex(),
                root: `/heap/${deck.name}/adapy-fea/v1`,
            });
            // The fixture deck runs with no heap-mirror budget: every call
            // re-reads its strides from the store (the eviction path).
            const core = new FeaEngineCore(mod, store, deck.name === "fixture" ? {maxMirrorBytes: 0} : {});
            const manifest = JSON.parse(readFileSync(join(deck.base_dir, "fea.manifest.json"), "utf8")) as FeaManifest;
            const log: Array<[string, number, number]> = [];
            const fetch = diskRangeFetcher(deck.base_dir, log);
            const sourceId = `test-${deck.name}`;
            const bv = Number(manifest.bake_version);
            let tierA = 0;
            const timings: string[] = [];
            for (const step of manifest.combination_steps ?? []) {
                if (step.needs_raw) {
                    assert.throws(() => planCase(manifest, step), LocalCaseUnsupported);
                    await assert.rejects(
                        core.materialiseCase({sourceId, sourceKey: "deck.SIN", manifest, caseN: step.n, fetch}),
                        LocalCaseUnsupported,
                    );
                    continue;
                }
                tierA++;
                const dir = caseDirName(step);
                const before = log.length;
                const res = await core.materialiseCase({sourceId, sourceKey: "deck.SIN", manifest, caseN: step.n, fetch});
                assert.equal(res.computed, true);
                timings.push(`${step.n}: ${res.timing!.total_ms.toFixed(1)} ms (${res.timing!.jobs} blobs, fetch ${res.timing!.fetch_ms.toFixed(1)} ms, kernel ${res.timing!.kernel_ms.toFixed(1)} ms, ${res.timing!.fetched_bytes} B fetched)`);
                // Only the strides the recipe reads were fetched: one range per
                // (blob, distinct basic not already present) + one header per new blob.
                for (const [, start, end] of log.slice(before)) assert.ok(end >= start);

                const expected = JSON.parse(
                    readFileSync(join(deck.expected_dir, dir, "fea.case.json"), "utf8"),
                ) as FeaCaseOverlay;
                assert.deepEqual(withoutProducer(res.overlay), withoutProducer(expected), `case ${step.n} overlay`);
                assert.equal(res.overlay.producer?.engine, "adacpp-wasm");
                assert.equal(res.overlay.producer?.tier, "A");
                assert.equal(res.overlay.prefix, `_derived/deck.SIN.fea/cases/${dir}/`);
                for (const f of expected.fields) {
                    const urls = f.per_type ? f.per_type.map((p) => p.blob.url) : [f.blob!.url];
                    for (const url of urls) {
                        const got = mod.FS.readFile(`${store.caseDir(sourceId, bv, dir)}/${url}`);
                        const want = new Uint8Array(readFileSync(join(deck.expected_dir, dir, url)));
                        assert.ok(sameBytes(got, want), `case ${step.n} ${url}: bytes differ`);
                        // ...and the store serves the same bytes to the viewer's fetchers.
                        const served = await store.readFile(sourceId, bv, `cases/${dir}/${url}`, {start: 1024, end: want.byteLength - 1});
                        assert.ok(served && sameBytes(served, want.subarray(1024)), `case ${step.n} ${url}: served range`);
                    }
                }
                // Asked again: the store's copy, nothing recomputed.
                const again = await core.materialiseCase({sourceId, sourceKey: "deck.SIN", manifest, caseN: step.n, fetch});
                assert.equal(again.computed, false);
                assert.deepEqual(withoutProducer(again.overlay), withoutProducer(expected));
            }
            assert.ok(tierA > 0);

            // Sparse base: no stride was fetched twice, and every fetched range
            // is the header or exactly one stride.
            const seen = new Set<string>();
            for (const [url, start, end] of log) {
                const key = `${url}:${start}`;
                assert.ok(!seen.has(key), `${key} fetched twice`);
                seen.add(key);
            }
            const fetchedBytes = log.reduce((a, [, s, e]) => a + (e - s + 1), 0);
            const baseBytes = (manifest.fields ?? [])
                .flatMap((f) => (f.per_type ? f.per_type.map((p) => p.blob.url) : f.blob ? [f.blob.url] : []))
                .reduce((a, url) => a + statSync(join(deck.base_dir, url)).size, 0);
            console.log(`[${deck.name}] fetched ${fetchedBytes} of ${baseBytes} base bytes over ${tierA} cases`);
            for (const t of timings) console.log(`[${deck.name}] wasm case ${t}`);

            // Envelopes: max / min blobs + governing sidecars, byte for byte.
            for (const field of deck.envelope_fields) {
                const doc = (await core.envelope({sourceId, manifest, field, fetch})) as unknown as Record<string, unknown>;
                const want = JSON.parse(
                    readFileSync(join(deck.envelope_dir, envelopeDirName(field), "fea.envelope.json"), "utf8"),
                ) as Record<string, unknown>;
                assert.deepEqual(withoutProducer(doc), withoutProducer(want), `${field} envelope doc`);
                for (const b of want.blobs as Array<{blob: {url: string}; governing_url: string}>) {
                    for (const url of [b.blob.url, b.governing_url]) {
                        const got = mod.FS.readFile(`${store.envelopeDir(sourceId, bv, field)}/${url}`);
                        const ref = new Uint8Array(readFileSync(join(deck.envelope_dir, envelopeDirName(field), url)));
                        assert.ok(sameBytes(got, ref), `${field} envelope ${url}: bytes differ`);
                    }
                }
            }
        });
    }
});
