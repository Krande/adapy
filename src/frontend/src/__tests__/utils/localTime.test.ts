// A zone that is not UTC and observes DST, so both the offset and the
// summer/winter switch are exercised. Any such zone would do.
process.env.TZ = "Europe/Berlin";

import assert from "node:assert/strict";
import {test} from "node:test";

import {localDate, localDateTime} from "../../utils/time";

// Wire values are UTC with an explicit offset; display is the only place a
// shift belongs. These pin that the shift actually happens, in a zone where
// UTC and local differ.

test("a UTC instant renders at the viewer's wall-clock time", () => {
    // 10:07 UTC is 12:07 at +02:00 (summer offset).
    assert.equal(localDateTime("2026-09-03T10:07:24+00:00"), "2026-09-03 12:07:24");
});

test("a late-evening UTC instant lands on the NEXT local day", () => {
    // The bug this closes: 23:30 UTC is already the 4th in Oslo, and a
    // date-only rendering off the raw UTC string showed the 3rd.
    assert.equal(localDate("2026-09-03T23:30:00+00:00"), "2026-09-04");
});

test("an early-morning local instant keeps its own day", () => {
    // 01:30 local on the 4th is 23:30 UTC on the 3rd — the same trap, inverted.
    assert.equal(localDate("2026-09-03T23:30:00Z"), "2026-09-04");
});

test("winter uses the offset in force then, not a fixed one", () => {
    // The zone is +01:00 in January; hardcoding +02:00 would be wrong half the year.
    assert.equal(localDateTime("2026-01-15T23:30:00+00:00"), "2026-01-16 00:30:00");
});

test("output stays ISO-shaped so it sorts", () => {
    assert.match(localDate("2026-09-03T10:07:24+00:00"), /^\d{4}-\d{2}-\d{2}$/);
    assert.match(localDateTime("2026-09-03T10:07:24+00:00"), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
});

test("absent and unparseable values render as the fallback, not Invalid Date", () => {
    for (const bad of [null, undefined, "", "not-a-date"]) {
        assert.equal(localDate(bad), "");
        assert.equal(localDateTime(bad), "");
    }
    assert.equal(localDate(null, "—"), "—");
});

test("a Date and an epoch are accepted as well as a string", () => {
    const iso = "2026-09-03T10:07:24+00:00";
    assert.equal(localDateTime(new Date(iso)), "2026-09-03 12:07:24");
    assert.equal(localDateTime(Date.parse(iso)), "2026-09-03 12:07:24");
});
