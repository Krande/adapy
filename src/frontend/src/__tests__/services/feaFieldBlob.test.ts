import {describe, it, beforeEach} from "node:test";
import assert from "node:assert/strict";

import {
    parseFieldBlob,
    fetchFieldStep,
    clearFieldBlobCache,
} from "../../services/feaFieldBlob";
import type {FeaFetcher, FeaRangeFetcher} from "../../services/fea/feaFetcher";
import type {FeaManifestField} from "../../services/viewerApi";

const HEADER_BYTES = 1024;

function buildAfbl({
    n_steps,
    n_points,
    n_components,
    fillStep,
    badMagic,
    overflow,
}: {
    n_steps: number;
    n_points: number;
    n_components: number;
    fillStep: (i: number, out: Float32Array) => void;
    badMagic?: boolean;
    overflow?: boolean;
}): ArrayBuffer {
    const stride_bytes = n_points * n_components * 4;
    // Allocate slack for the overflow test, which deliberately writes
    // a JSON header bigger than the fixed 1024-byte prefix to verify
    // parseFieldBlob rejects it. Without slack the test helper itself
    // would throw before the parser sees the bytes.
    const slack = overflow ? 4096 : 0;
    const total = HEADER_BYTES + slack + n_steps * stride_bytes;
    const buf = new ArrayBuffer(total);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);

    // Magic "AFBL" (or scrambled).
    if (badMagic) {
        u8.set([0x58, 0x58, 0x58, 0x58], 0); // "XXXX"
    } else {
        u8.set([0x41, 0x46, 0x42, 0x4c], 0); // "AFBL"
    }
    dv.setUint32(4, 1, true); // version

    const headerObj = {
        name: "DEPL",
        n_steps,
        n_points,
        n_components,
        dtype: "float32",
        stride_bytes,
    };
    if (overflow) {
        // Make the JSON longer than the prefix — should be rejected.
        (headerObj as Record<string, unknown>).pad = "x".repeat(2048);
    }
    const json = new TextEncoder().encode(JSON.stringify(headerObj));
    dv.setUint32(8, json.length, true);
    u8.set(json, 12);

    // Step payloads, step-major.
    for (let i = 0; i < n_steps; i++) {
        const offset = HEADER_BYTES + i * stride_bytes;
        const view = new Float32Array(buf, offset, n_points * n_components);
        fillStep(i, view);
    }
    return buf;
}

describe("parseFieldBlob", () => {
    it("parses a 3-step vector6 field and exposes per-step views", () => {
        const buf = buildAfbl({
            n_steps: 3,
            n_points: 4,
            n_components: 6,
            fillStep: (i, out) => {
                for (let v = 0; v < 4; v++) {
                    for (let c = 0; c < 6; c++) {
                        // Encode (step, vertex, component) into the value
                        // so the test can assert exact retrieval.
                        out[v * 6 + c] = i * 100 + v * 10 + c;
                    }
                }
            },
        });
        const parsed = parseFieldBlob(buf);
        assert.equal(parsed.header.name, "DEPL");
        assert.equal(parsed.header.n_steps, 3);
        assert.equal(parsed.header.n_points, 4);
        assert.equal(parsed.header.n_components, 6);
        assert.equal(parsed.header.dtype, "float32");
        assert.equal(parsed.header.stride_bytes, 4 * 6 * 4);
        assert.equal(parsed.steps.length, 3);
        for (let i = 0; i < 3; i++) {
            assert.equal(parsed.steps[i].length, 4 * 6);
            // Spot-check: step i, vertex 2, component 3 = i*100 + 23.
            assert.equal(parsed.steps[i][2 * 6 + 3], i * 100 + 23);
        }
    });

    it("step views share the underlying buffer (no extra alloc)", () => {
        const buf = buildAfbl({
            n_steps: 2,
            n_points: 1,
            n_components: 3,
            fillStep: (i, out) => {
                out[0] = i;
                out[1] = i * 2;
                out[2] = i * 3;
            },
        });
        const parsed = parseFieldBlob(buf);
        // The Float32Array views must be backed by the same buffer
        // we passed in — confirms the parser doesn't copy step data.
        assert.equal(parsed.steps[0].buffer, buf);
        assert.equal(parsed.steps[1].buffer, buf);
    });

    it("rejects bad magic", () => {
        const buf = buildAfbl({
            n_steps: 1,
            n_points: 1,
            n_components: 1,
            fillStep: () => {},
            badMagic: true,
        });
        assert.throws(() => parseFieldBlob(buf), /bad magic/);
    });

    it("rejects a header that overflows the fixed prefix", () => {
        const buf = buildAfbl({
            n_steps: 1,
            n_points: 1,
            n_components: 1,
            fillStep: () => {},
            overflow: true,
        });
        assert.throws(() => parseFieldBlob(buf), /overflows fixed prefix/);
    });

    it("rejects a buffer that's smaller than the declared payload", () => {
        // Build a valid blob, then truncate the underlying buffer.
        const buf = buildAfbl({
            n_steps: 5,
            n_points: 10,
            n_components: 3,
            fillStep: () => {},
        });
        // Slice off half the payload.
        const truncated = buf.slice(0, HEADER_BYTES + 2 * 10 * 3 * 4);
        assert.throws(() => parseFieldBlob(truncated), /payload/);
    });
});

describe("fetchFieldStep (per-step HTTP-Range)", () => {
    const N_STEPS = 6;
    const N_POINTS = 4;
    const N_COMPONENTS = 3;
    const STRIDE = N_POINTS * N_COMPONENTS * 4;

    function makeBlob(): ArrayBuffer {
        return buildAfbl({
            n_steps: N_STEPS,
            n_points: N_POINTS,
            n_components: N_COMPONENTS,
            fillStep: (i, out) => {
                for (let k = 0; k < out.length; k++) out[k] = i * 1000 + k;
            },
        });
    }

    function makeField(): FeaManifestField {
        return {
            name_canonical: "DEPL",
            category: "displacement",
            support: "nodal",
            blob: {
                url: "fea.DEPL.bin",
                header_bytes: HEADER_BYTES,
                stride_bytes: STRIDE,
                dtype: "float32",
                byte_order: "little",
            },
            n_steps: N_STEPS,
        } as unknown as FeaManifestField;
    }

    const wholeFetcher = (blob: ArrayBuffer): FeaFetcher => async () => blob;

    beforeEach(() => clearFieldBlobCache());

    it("ranged response: fetches only the requested step's stride", async () => {
        const blob = makeBlob();
        const calls: Array<{start: number; end: number}> = [];
        const ranged: FeaRangeFetcher = async (_f, start, end) => {
            calls.push({start, end});
            return {buf: blob.slice(start, end + 1), ranged: true};
        };

        const step3 = await fetchFieldStep(ranged, wholeFetcher(blob), makeField(), 3, "ck");
        assert.equal(step3.length, N_POINTS * N_COMPONENTS);
        assert.equal(step3[0], 3000);
        assert.equal(step3[5], 3005);
        // Exactly one fetch, covering just step 3's window.
        assert.equal(calls.length, 1);
        assert.equal(calls[0].start, HEADER_BYTES + 3 * STRIDE);
        assert.equal(calls[0].end, HEADER_BYTES + 4 * STRIDE - 1);
    });

    it("caches per step: a repeat fetch issues no new request", async () => {
        const blob = makeBlob();
        let n = 0;
        const ranged: FeaRangeFetcher = async (_f, start, end) => {
            n++;
            return {buf: blob.slice(start, end + 1), ranged: true};
        };
        const field = makeField();
        await fetchFieldStep(ranged, wholeFetcher(blob), field, 2, "ck");
        await fetchFieldStep(ranged, wholeFetcher(blob), field, 2, "ck");
        assert.equal(n, 1);
    });

    it("non-ranged (legacy gzip) response: parses whole blob, then slices for free", async () => {
        const blob = makeBlob();
        let n = 0;
        // Server ignored Range and returned the whole object (200).
        const ranged: FeaRangeFetcher = async () => {
            n++;
            return {buf: blob, ranged: false};
        };
        const field = makeField();
        const step1 = await fetchFieldStep(ranged, wholeFetcher(blob), field, 1, "ck");
        assert.equal(step1[0], 1000);
        // A different step now comes from the cached whole blob — no 2nd fetch.
        const step4 = await fetchFieldStep(ranged, wholeFetcher(blob), field, 4, "ck");
        assert.equal(step4[0], 4000);
        assert.equal(n, 1);
    });

    it("range fetch throws ('Failed to fetch'): falls back to whole-blob fetcher", async () => {
        const blob = makeBlob();
        let wholeCalls = 0;
        const ranged: FeaRangeFetcher = async () => {
            throw new TypeError("Failed to fetch");
        };
        const fetcher: FeaFetcher = async () => {
            wholeCalls++;
            return blob;
        };
        const field = makeField();
        const step2 = await fetchFieldStep(ranged, fetcher, field, 2, "ck");
        assert.equal(step2[0], 2000);
        assert.equal(wholeCalls, 1);
        // Range is now disabled session-wide → a 2nd field load uses the
        // whole-blob fetcher directly without attempting another range.
        clearFieldBlobCache();
        let rangedAttempts = 0;
        const ranged2: FeaRangeFetcher = async () => {
            rangedAttempts++;
            return {buf: blob, ranged: true};
        };
        const step3 = await fetchFieldStep(ranged2, wholeFetcher(blob), field, 3, "ck");
        assert.equal(step3[0], 3000);
        assert.equal(rangedAttempts, 0, "range should stay disabled after a failure");
    });
});
