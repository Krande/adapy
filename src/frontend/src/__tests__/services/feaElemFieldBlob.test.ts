import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {parseElemFieldBlob} from "../../services/feaElemFieldBlob";

const HEADER_BYTES = 1024;

function buildAfel({
    n_steps,
    n_elements,
    n_ips,
    n_components,
    fillStep,
    badMagic,
    overflow,
}: {
    n_steps: number;
    n_elements: number;
    n_ips: number;
    n_components: number;
    fillStep: (i: number, out: Float32Array) => void;
    badMagic?: boolean;
    overflow?: boolean;
}): ArrayBuffer {
    const stride_bytes = n_elements * n_ips * n_components * 4;
    const slack = overflow ? 4096 : 0;
    const total = HEADER_BYTES + slack + n_steps * stride_bytes;
    const buf = new ArrayBuffer(total);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);

    if (badMagic) {
        u8.set([0x58, 0x58, 0x58, 0x58], 0); // "XXXX"
    } else {
        u8.set([0x41, 0x46, 0x45, 0x4c], 0); // "AFEL"
    }
    dv.setUint32(4, 1, true);

    const headerObj = {
        name: "STRESS",
        elem_type: "quad",
        n_steps,
        n_elements,
        n_ips,
        n_components,
        dtype: "float32",
        stride_bytes,
    };
    if (overflow) {
        (headerObj as Record<string, unknown>).pad = "x".repeat(2048);
    }
    const json = new TextEncoder().encode(JSON.stringify(headerObj));
    dv.setUint32(8, json.length, true);
    u8.set(json, 12);

    for (let i = 0; i < n_steps; i++) {
        const offset = HEADER_BYTES + i * stride_bytes;
        const view = new Float32Array(buf, offset, n_elements * n_ips * n_components);
        fillStep(i, view);
    }
    return buf;
}

describe("parseElemFieldBlob", () => {
    it("parses a 2-step (3 elements × 4 IPs × 6 components) blob", () => {
        const buf = buildAfel({
            n_steps: 2,
            n_elements: 3,
            n_ips: 4,
            n_components: 6,
            fillStep: (i, out) => {
                // Encode (step, elem, ip, comp) so retrieval can be
                // checked exactly.
                for (let e = 0; e < 3; e++) {
                    for (let ip = 0; ip < 4; ip++) {
                        for (let c = 0; c < 6; c++) {
                            out[e * 4 * 6 + ip * 6 + c] =
                                i * 10000 + e * 1000 + ip * 100 + c;
                        }
                    }
                }
            },
        });
        const parsed = parseElemFieldBlob(buf);
        assert.equal(parsed.header.name, "STRESS");
        assert.equal(parsed.header.elem_type, "quad");
        assert.equal(parsed.header.n_steps, 2);
        assert.equal(parsed.header.n_elements, 3);
        assert.equal(parsed.header.n_ips, 4);
        assert.equal(parsed.header.n_components, 6);
        assert.equal(parsed.steps.length, 2);
        assert.equal(parsed.steps[0].length, 3 * 4 * 6);
        // Spot-check (step=1, elem=2, ip=3, comp=4) = 10000 + 2000 + 300 + 4.
        assert.equal(
            parsed.steps[1][2 * 4 * 6 + 3 * 6 + 4],
            10000 + 2000 + 300 + 4,
        );
    });

    it("step views are zero-copy slices of the source buffer", () => {
        const buf = buildAfel({
            n_steps: 2,
            n_elements: 1,
            n_ips: 1,
            n_components: 3,
            fillStep: (i, out) => {
                out[0] = i;
                out[1] = i * 2;
                out[2] = i * 3;
            },
        });
        const parsed = parseElemFieldBlob(buf);
        assert.equal(parsed.steps[0].buffer, buf);
        assert.equal(parsed.steps[1].buffer, buf);
    });

    it("rejects bad magic", () => {
        const buf = buildAfel({
            n_steps: 1,
            n_elements: 1,
            n_ips: 1,
            n_components: 1,
            fillStep: () => {},
            badMagic: true,
        });
        assert.throws(() => parseElemFieldBlob(buf), /bad magic/);
    });

    it("rejects a header that overflows the fixed prefix", () => {
        const buf = buildAfel({
            n_steps: 1,
            n_elements: 1,
            n_ips: 1,
            n_components: 1,
            fillStep: () => {},
            overflow: true,
        });
        assert.throws(() => parseElemFieldBlob(buf), /overflows fixed prefix/);
    });

    it("rejects a truncated payload", () => {
        const buf = buildAfel({
            n_steps: 4,
            n_elements: 2,
            n_ips: 2,
            n_components: 3,
            fillStep: () => {},
        });
        const truncated = buf.slice(0, HEADER_BYTES + 2 * 2 * 2 * 3 * 4);
        assert.throws(() => parseElemFieldBlob(truncated), /payload/);
    });
});
