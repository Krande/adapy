import assert from "node:assert/strict";
import {test} from "node:test";

import {fuzzyFilter, fuzzyMatch} from "@/services/fuzzy";

// The names this is written for. One collection is one entry per E3D SITE
// across every model file of a project, so they are long, structured, and
// differ in a few characters near the end.
const SITES = [
    "ModelExportMain.rvm~AP400-STRU_MS",
    "ModelExportMain.rvm~AP400-STRU",
    "ModelExportMain.rvm~AP400-ELEC",
    "ModelExportMain.rvm~AP4000-ELEC_VAT",
    "ModelExportTempSteel.rvm~AP400-STRU_TS",
    "ModelExportMain.rvm~AP300-MECH",
];

test("an empty query keeps everything and sorts it by name", () => {
    const out = fuzzyFilter(SITES, "", (s) => s);
    assert.equal(out.length, SITES.length);
    assert.deepEqual(out, [...out].sort((a, b) => a.localeCompare(b, undefined, {numeric: true})));
});

test("sorting is numeric, so AP400 comes before AP4000", () => {
    const out = fuzzyFilter(["AP4000-ELEC", "AP400-STRU", "AP40-PIPE"], "", (s) => s);
    assert.deepEqual(out, ["AP40-PIPE", "AP400-STRU", "AP4000-ELEC"]);
});

test("a subsequence matches without the separators being typed", () => {
    // The point of the whole file: this is how someone thinks of that site, and
    // `includes` finds nothing for it.
    const out = fuzzyFilter(SITES, "ap400ms", (s) => s);
    assert.equal(out[0], "ModelExportMain.rvm~AP400-STRU_MS");
});

test("matching is case-insensitive", () => {
    assert.ok(fuzzyMatch("STRU", "ModelExportMain.rvm~AP400-stru"));
    assert.ok(fuzzyMatch("stru", "ModelExportMain.rvm~AP400-STRU"));
});

test("a query whose characters are out of order does not match", () => {
    // Subsequence, not "contains these letters somewhere".
    assert.equal(fuzzyMatch("urts", "AP400-STRU"), null);
});

test("only candidates that actually contain the query are kept", () => {
    const out = fuzzyFilter(SITES, "elec", (s) => s);
    assert.ok(out.length > 0);
    assert.ok(out.every((s) => fuzzyMatch("elec", s) !== null));
});

test("a contiguous run outranks the same characters spread out", () => {
    // Same length, same lack of word breaks, so contiguity is the ONLY thing
    // separating them. Comparing against a candidate whose every character sits
    // after a hyphen would not test this: each of those earns a word-start
    // bonus, which is meant to be worth more than contiguity and is what makes
    // "elec" find the ELEC in a site name rather than the "ele" in
    // "ModelExport".
    const tight = fuzzyMatch("stru", "xxstruxxxxx")!;
    const loose = fuzzyMatch("stru", "xsxtxrxuxxx")!;
    assert.ok(tight.score > loose.score, `${tight.score} should beat ${loose.score}`);
});

test("a word start outranks a contiguous run buried in a shared prefix", () => {
    // The case that made a greedy matcher useless here: every name in a
    // collection begins `ModelExport`, which contains "ele" contiguously, so a
    // greedy match never reaches the ELEC anyone was actually looking for.
    const out = fuzzyFilter(SITES, "elec", (s) => s);
    assert.equal(out[0], "ModelExportMain.rvm~AP400-ELEC");
    assert.deepEqual(fuzzyMatch("elec", out[0])!.positions, [26, 27, 28, 29]);
});

test("a shorter name that spent most of itself matching ranks higher", () => {
    const out = fuzzyFilter(["AP400-STRU", "ModelExportMain.rvm~AP400-STRU-EXTRA-LONG"], "ap400stru", (s) => s);
    assert.equal(out[0], "AP400-STRU");
});

test("positions index the candidate in order", () => {
    const m = fuzzyMatch("aps", "AP400-STRU")!;
    assert.deepEqual(m.positions, [0, 1, 6]);
    assert.equal("AP400-STRU"[6], "S");
    assert.deepEqual([...m.positions].sort((a, b) => a - b), m.positions);
});

test("an empty query is a match, so one code path serves filtered and unfiltered", () => {
    const m = fuzzyMatch("   ", "anything")!;
    assert.equal(m.score, 0);
    assert.deepEqual(m.positions, []);
});

test("no match returns an empty list rather than everything", () => {
    assert.deepEqual(fuzzyFilter(SITES, "zzzz", (s) => s), []);
});
