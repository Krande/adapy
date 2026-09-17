import assert from "node:assert/strict";
import {test} from "node:test";

import {fuzzyFilter, fuzzyMatch} from "@/services/fuzzy";

// The names this is written for. One collection may be one entry per SITE
// across every model file of a project, so they are long, structured, and
// differ in a few characters near the end.
const SITES = [
    "ExportMain.rvm~site-one",
    "ExportMain.rvm~site",
    "ExportMain.rvm~elec-one",
    "ExportMain.rvm~elec-two_VAT",
    "ExportOther.rvm~site-two",
    "ExportMain.rvm~mech",
];

test("an empty query keeps everything and sorts it by name", () => {
    const out = fuzzyFilter(SITES, "", (s) => s);
    assert.equal(out.length, SITES.length);
    assert.deepEqual(out, [...out].sort((a, b) => a.localeCompare(b, undefined, {numeric: true})));
});

test("sorting is numeric, so item400 comes before item4000", () => {
    const out = fuzzyFilter(["item4000", "item400", "item40"], "", (s) => s);
    assert.deepEqual(out, ["item40", "item400", "item4000"]);
});

test("a subsequence matches without the separators being typed", () => {
    // The point of the whole file: this is how someone thinks of that site, and
    // `includes` finds nothing for it.
    const out = fuzzyFilter(SITES, "mainsiteone", (s) => s);
    assert.equal(out[0], "ExportMain.rvm~site-one");
});

test("matching is case-insensitive", () => {
    assert.ok(fuzzyMatch("SITE", "ExportMain.rvm~site"));
    assert.ok(fuzzyMatch("site", "ExportMain.rvm~SITE"));
});

test("a query whose characters are out of order does not match", () => {
    // Subsequence, not "contains these letters somewhere".
    assert.equal(fuzzyMatch("etis", "item-site"), null);
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
    // "elec" find the ELEC in a site name rather than the shared prefix.
    const tight = fuzzyMatch("site", "xxsitexxxxx")!;
    const loose = fuzzyMatch("site", "xsxixtxexxx")!;
    assert.ok(tight.score > loose.score, `${tight.score} should beat ${loose.score}`);
});

test("a word start outranks a contiguous run buried in a shared prefix", () => {
    // The case that made a greedy matcher useless here: every name in a
    // collection begins `ExportMain`, which contains "ain" contiguously, so a
    // greedy match never reaches the ELEC anyone was actually looking for.
    const out = fuzzyFilter(SITES, "elec", (s) => s);
    assert.equal(out[0], "ExportMain.rvm~elec-one");
    assert.deepEqual(fuzzyMatch("elec", out[0])!.positions, [15, 16, 17, 18]);
});

test("a shorter name that spent most of itself matching ranks higher", () => {
    const out = fuzzyFilter(["item-site", "ExportMain.rvm~item-site-EXTRA-LONG"], "itemsite", (s) => s);
    assert.equal(out[0], "item-site");
});

test("positions index the candidate in order", () => {
    const m = fuzzyMatch("its", "item-site")!;
    assert.deepEqual(m.positions, [0, 1, 5]);
    assert.equal("item-site"[5], "s");
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
