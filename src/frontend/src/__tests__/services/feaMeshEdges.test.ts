import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {parseMeshEdges} from "../../services/feaMeshEdges";

const HEADER_BYTES = 16;

function buildAfeg({
    pairs,
    badMagic,
    truncate,
}: {
    pairs: Array<[number, number]>;
    badMagic?: boolean;
    truncate?: boolean;
}): ArrayBuffer {
    const n_edges = pairs.length;
    const payloadBytes = truncate ? Math.max(0, n_edges * 8 - 4) : n_edges * 8;
    const buf = new ArrayBuffer(HEADER_BYTES + payloadBytes);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);

    if (badMagic) {
        u8.set([0x58, 0x58, 0x58, 0x58], 0); // "XXXX"
    } else {
        u8.set([0x41, 0x46, 0x45, 0x47], 0); // "AFEG"
    }
    dv.setUint32(4, 1, true); // version
    dv.setUint32(8, n_edges, true); // n_edges
    // bytes 12..15 = zero pad

    if (!truncate) {
        for (let i = 0; i < n_edges; i++) {
            dv.setUint32(HEADER_BYTES + i * 8, pairs[i][0], true);
            dv.setUint32(HEADER_BYTES + i * 8 + 4, pairs[i][1], true);
        }
    }
    return buf;
}

describe("parseMeshEdges", () => {
    it("parses a 3-edge sidecar into a flat 6-element index list", () => {
        const buf = buildAfeg({
            pairs: [
                [0, 1],
                [1, 2],
                [2, 0],
            ],
        });
        const indices = parseMeshEdges(buf);
        assert.equal(indices.length, 6);
        assert.deepEqual(Array.from(indices), [0, 1, 1, 2, 2, 0]);
    });

    it("returns an empty array on n_edges = 0 (no payload required)", () => {
        const buf = buildAfeg({pairs: []});
        const indices = parseMeshEdges(buf);
        assert.equal(indices.length, 0);
    });

    it("rejects bad magic", () => {
        const buf = buildAfeg({
            pairs: [[0, 1]],
            badMagic: true,
        });
        assert.throws(() => parseMeshEdges(buf), /bad magic/);
    });

    it("rejects a truncated payload", () => {
        const buf = buildAfeg({
            pairs: [
                [0, 1],
                [1, 2],
            ],
            truncate: true,
        });
        assert.throws(() => parseMeshEdges(buf), /payload/);
    });

    it("the returned view shares the input buffer (no extra alloc)", () => {
        const buf = buildAfeg({
            pairs: [[7, 11]],
        });
        const indices = parseMeshEdges(buf);
        assert.equal(indices.buffer, buf);
    });
});
