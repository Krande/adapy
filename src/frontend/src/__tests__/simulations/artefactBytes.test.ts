// Reading a run artefact's bytes, whole or from an offset: what the server's answer means.
import assert from "node:assert/strict";
import {test} from "node:test";

import {artefactBytesFrom} from "@/services/api/simulations";

const body = Uint8Array.from([10, 11, 12, 13, 14, 15]);

test("a whole artefact is its bytes and its length", () => {
    assert.deepEqual(artefactBytesFrom(200, null, body, 0, undefined), {bytes: body, start: 0, total: 6});
});

test("a served range is the part, with the artefact's size from Content-Range", () => {
    const part = body.subarray(4);
    assert.deepEqual(artefactBytesFrom(206, "bytes 4-5/6", part, 4, undefined), {bytes: part, start: 4, total: 6});
});

test("nothing beyond the offset yet is no bytes, not an error", () => {
    const read = artefactBytesFrom(416, "bytes */6", new Uint8Array(0), 6, undefined)!;
    assert.equal(read.bytes.length, 0);
    assert.equal(read.start, 6);
    assert.equal(read.total, 6);
});

test("a server that cannot serve a part sends the whole artefact: the part is cut out here", () => {
    assert.deepEqual([...artefactBytesFrom(200, null, body, 2, undefined)!.bytes], [12, 13, 14, 15]);
    assert.deepEqual([...artefactBytesFrom(200, null, body, 1, 3)!.bytes], [11, 12, 13], "the end is inclusive");
    assert.deepEqual([...artefactBytesFrom(200, null, body, 9, undefined)!.bytes], [], "an offset beyond the end");
    assert.equal(artefactBytesFrom(200, null, body, 2, undefined)!.total, 6);
});

test("any other status is not an answer", () => {
    assert.equal(artefactBytesFrom(404, null, new Uint8Array(0), 0, undefined), null);
    assert.equal(artefactBytesFrom(500, null, new Uint8Array(0), 3, undefined), null);
});
