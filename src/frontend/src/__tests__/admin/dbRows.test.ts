import assert from "node:assert/strict";
import test from "node:test";

import {
    cellView,
    CELL_PREVIEW_CHARS,
    hasNext,
    hasPrev,
    nextOffset,
    pageLabel,
    prevOffset,
    ROWS_PAGE_SIZE,
} from "@/components/admin/database/dbRows";

const page = (offset: number, count: number, total: number, totalExact: boolean) => ({
    offset,
    count,
    limit: ROWS_PAGE_SIZE,
    total,
    totalExact,
});

test("a page says which rows it holds, an estimate marked as one", () => {
    assert.equal(pageLabel(page(0, 50, 120, true)), "rows 1–50 of 120");
    assert.equal(pageLabel(page(100, 20, 120, true)), "rows 101–120 of 120");
    assert.equal(pageLabel(page(50, 50, 1_268_101, false)), "rows 51–100 of ~1.3M");
    assert.equal(pageLabel(page(0, 0, 0, true)), "no rows");
    assert.equal(pageLabel(page(1200, 0, 1100, false)), "no rows past 1,200");
});

test("prev and next stop at the ends", () => {
    assert.equal(hasPrev(page(0, 50, 120, true)), false);
    assert.equal(hasPrev(page(50, 50, 120, true)), true);
    assert.equal(hasNext(page(0, 50, 120, true)), true);
    assert.equal(hasNext(page(50, 50, 100, true)), false); // exactly at the end
    assert.equal(hasNext(page(100, 20, 120, true)), false);
    // An estimate cannot say where the end is: a full page may have more, a short one has not.
    assert.equal(hasNext(page(50, 50, 60, false)), true);
    assert.equal(hasNext(page(100, 7, 5000, false)), false);
    assert.equal(prevOffset({offset: 30, limit: 50}), 0);
    assert.equal(prevOffset({offset: 100, limit: 50}), 50);
    assert.equal(nextOffset({offset: 100, limit: 50}), 150);
});

test("cells fit a column and keep the whole value for hover", () => {
    assert.deepEqual(cellView(null), {text: "null", full: "null", clipped: false, serverCut: false, kind: "null"});
    assert.equal(cellView(42).text, "42");
    assert.equal(cellView(false).text, "false");
    assert.equal(cellView("<redacted>").kind, "redacted");
    assert.equal(cellView("<1234 bytes>").kind, "bytes");
    const long = "x".repeat(200);
    const v = cellView(long);
    assert.equal(v.text.length, CELL_PREVIEW_CHARS);
    assert.ok(v.text.endsWith("…") && v.clipped && v.full === long && !v.serverCut);
    const cut = cellView("y".repeat(500) + "…[truncated]");
    assert.ok(cut.serverCut && cut.clipped);
    const multi = cellView("a\n  b");
    assert.equal(multi.text, "a ⏎ b");
    assert.ok(multi.clipped && multi.full === "a\n  b");
    assert.equal(cellView("short").clipped, false);
});
