import {describe, it} from "node:test";
import assert from "node:assert/strict";

import {parseBeamSolidsWarp} from "../../services/feaBeamSolidsWarp";

const HEADER_BYTES = 16;
const ENTRY_BYTES = 12;

function buildAfbv({
    n_verts,
    n0,
    n1,
    t,
    badMagic,
}: {
    n_verts: number;
    n0: number[];
    n1: number[];
    t: number[];
    badMagic?: boolean;
}): ArrayBuffer {
    const buf = new ArrayBuffer(HEADER_BYTES + n_verts * ENTRY_BYTES);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);
    if (badMagic) {
        u8.set([0x58, 0x58, 0x58, 0x58], 0); // "XXXX"
    } else {
        u8.set([0x41, 0x46, 0x42, 0x56], 0); // "AFBV"
    }
    dv.setUint32(4, 1, true); // version
    dv.setUint32(8, n_verts, true);
    for (let i = 0; i < n_verts; i++) {
        const off = HEADER_BYTES + i * ENTRY_BYTES;
        dv.setUint32(off, n0[i], true);
        dv.setUint32(off + 4, n1[i], true);
        dv.setFloat32(off + 8, t[i], true);
    }
    return buf;
}

describe("parseBeamSolidsWarp", () => {
    it("parses (n0, n1, t) records and exposes three typed arrays", () => {
        const buf = buildAfbv({
            n_verts: 4,
            n0: [10, 10, 22, 22],
            n1: [11, 11, 23, 23],
            t: [0.0, 0.5, 0.25, 1.0],
        });
        const parsed = parseBeamSolidsWarp(buf);
        assert.equal(parsed.n_verts, 4);
        assert.deepEqual(Array.from(parsed.node0), [10, 10, 22, 22]);
        assert.deepEqual(Array.from(parsed.node1), [11, 11, 23, 23]);
        // Float roundtrip — exact for the values we wrote.
        assert.equal(parsed.t[0], 0.0);
        assert.equal(parsed.t[1], 0.5);
        assert.equal(parsed.t[2], 0.25);
        assert.equal(parsed.t[3], 1.0);
    });

    it("handles a zero-vertex blob without allocating payload arrays", () => {
        const buf = buildAfbv({n_verts: 0, n0: [], n1: [], t: []});
        const parsed = parseBeamSolidsWarp(buf);
        assert.equal(parsed.n_verts, 0);
        assert.equal(parsed.node0.length, 0);
        assert.equal(parsed.node1.length, 0);
        assert.equal(parsed.t.length, 0);
    });

    it("rejects bad magic", () => {
        const buf = buildAfbv({
            n_verts: 1, n0: [0], n1: [1], t: [0.5], badMagic: true,
        });
        assert.throws(() => parseBeamSolidsWarp(buf), /bad magic/);
    });

    it("rejects a truncated payload", () => {
        const buf = buildAfbv({
            n_verts: 4, n0: [1, 2, 3, 4], n1: [5, 6, 7, 8], t: [0, 0, 0, 0],
        });
        const truncated = buf.slice(0, HEADER_BYTES + 2 * ENTRY_BYTES);
        assert.throws(() => parseBeamSolidsWarp(truncated), /payload/);
    });
});
