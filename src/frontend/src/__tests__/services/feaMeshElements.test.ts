import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {parseMeshElements} from "../../services/feaMeshElements";

const HEADER_BYTES = 16;
const ENTRY_BYTES = 12;

function buildAfem({
    entries,
    badMagic,
    truncate,
}: {
    entries: Array<[number, number, number]>;
    badMagic?: boolean;
    truncate?: boolean;
}): ArrayBuffer {
    const n = entries.length;
    const payloadBytes = truncate ? Math.max(0, n * ENTRY_BYTES - 4) : n * ENTRY_BYTES;
    const buf = new ArrayBuffer(HEADER_BYTES + payloadBytes);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);

    if (badMagic) {
        u8.set([0x58, 0x58, 0x58, 0x58], 0); // "XXXX"
    } else {
        u8.set([0x41, 0x46, 0x45, 0x4d], 0); // "AFEM"
    }
    dv.setUint32(4, 1, true); // version
    dv.setUint32(8, n, true); // n_elements
    // bytes 12..15 = zero pad

    if (!truncate) {
        for (let i = 0; i < n; i++) {
            const [label, start, count] = entries[i];
            dv.setUint32(HEADER_BYTES + i * ENTRY_BYTES + 0, label, true);
            dv.setUint32(HEADER_BYTES + i * ENTRY_BYTES + 4, start, true);
            dv.setUint32(HEADER_BYTES + i * ENTRY_BYTES + 8, count, true);
        }
    }
    return buf;
}

describe("parseMeshElements", () => {
    it("parses (label, tri_start, tri_count) triplets in iteration order", () => {
        const buf = buildAfem({
            entries: [
                [101, 0, 12],
                [102, 12, 12],
                [103, 24, 4],
            ],
        });
        const out = parseMeshElements(buf);
        assert.equal(out.length, 3);
        assert.deepEqual(out[0], {label: 101, triStart: 0, triCount: 12});
        assert.deepEqual(out[1], {label: 102, triStart: 12, triCount: 12});
        assert.deepEqual(out[2], {label: 103, triStart: 24, triCount: 4});
    });

    it("returns an empty array on n_elements = 0 (no payload required)", () => {
        const buf = buildAfem({entries: []});
        const out = parseMeshElements(buf);
        assert.equal(out.length, 0);
    });

    it("preserves zero tri_count for line elements (selectable later via edge buffer)", () => {
        const buf = buildAfem({
            entries: [
                [42, 0, 0], // line element — no tris owned
                [43, 0, 6], // shell element — owns 2 tris worth of indices
            ],
        });
        const out = parseMeshElements(buf);
        assert.equal(out[0].triCount, 0);
        assert.equal(out[1].triCount, 6);
    });

    it("rejects bad magic", () => {
        const buf = buildAfem({
            entries: [[1, 0, 1]],
            badMagic: true,
        });
        assert.throws(() => parseMeshElements(buf), /bad magic/);
    });

    it("rejects a truncated payload", () => {
        const buf = buildAfem({
            entries: [
                [1, 0, 4],
                [2, 4, 4],
            ],
            truncate: true,
        });
        assert.throws(() => parseMeshElements(buf), /payload/);
    });
});
