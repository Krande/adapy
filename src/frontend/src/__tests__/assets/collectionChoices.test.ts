import assert from "node:assert/strict";
import test from "node:test";

import {
    affectedChoices,
    collectionChoiceDeclsOfSpec,
    defaultChoiceLabel,
    itemChoiceLines,
    parseCollectionChoiceDecls,
    pickOf,
    pickOptions,
    picksOfSettings,
    picksSummary,
    settingsWithPicks,
    withPick,
} from "@/assets/collectionChoices";

const LISTING = [
    {
        option: "source_root",
        label: "Share",
        collections: {
            ALPHA: {
                default: "//a/1",
                choices: [
                    {value: "//a/1", label: "one (r1, 2026-01-01)"},
                    {value: "//a/2", label: "two (r2, 2026-02-01)"},
                ],
            },
            BETA: {default: "//b/1", choices: [{value: "//b/1", label: "only"}]}, // one choice: nothing to pick
        },
    },
];

test("the listing and a spec read to the same declarations, collections with 2+ choices only", () => {
    const decls = parseCollectionChoiceDecls(LISTING);
    assert.deepEqual(Object.keys(decls[0].collections), ["ALPHA"]);
    const spec = {
        asset_collection_choices: [{option: "source_root", label: "Share", field: "source_roots"}],
        source_roots: LISTING[0].collections,
    };
    assert.deepEqual(collectionChoiceDeclsOfSpec(spec), decls);
    assert.deepEqual(parseCollectionChoiceDecls("nonsense"), []);
});

test("a form offers the affected collections; the default is labelled and clears the override", () => {
    const decls = parseCollectionChoiceDecls(LISTING);
    assert.deepEqual(affectedChoices(decls, null).map((a) => a.collection), ["ALPHA"]);
    assert.deepEqual(affectedChoices(decls, ["alpha", "BETA", "ZETA"]).map((a) => a.collection), ["alpha"]);
    const entry = decls[0].collections.ALPHA;
    assert.equal(defaultChoiceLabel(entry), "(default: one (r1, 2026-01-01))");
    const picked = withPick({}, "source_root", "ALPHA", "//a/2", entry);
    assert.deepEqual(picked, {source_root: {ALPHA: "//a/2"}});
    assert.equal(pickOf(picked, "source_root", "alpha"), "//a/2");
    assert.deepEqual(withPick(picked, "source_root", "alpha", "//a/1", entry), {}); // the default: no override
    assert.deepEqual(withPick(picked, "source_root", "ALPHA", undefined, entry), {});
});

test("overrides read back on a schedule row, in settings, and on a run item", () => {
    const decls = parseCollectionChoiceDecls(LISTING);
    const settings = settingsWithPicks({sweep_target: "tree"}, {source_root: {ALPHA: "//a/2", GONE: "//x"}});
    assert.deepEqual(picksOfSettings(settings), {source_root: {ALPHA: "//a/2", GONE: "//x"}});
    assert.deepEqual(picksSummary(decls, picksOfSettings(settings)), ["Share ALPHA: two (r2, 2026-02-01)", "Share GONE: //x"]);
    assert.deepEqual(settingsWithPicks(settings, {}), {sweep_target: "tree"});
    assert.deepEqual(itemChoiceLines(decls, "ALPHA", {source_root: "//a/2"}), ["Share: two (r2, 2026-02-01)"]);
    assert.deepEqual(itemChoiceLines(decls, "ALPHA", {}), ["Share: the worker's choice"]);
    assert.deepEqual(itemChoiceLines(decls, "ALPHA", undefined), []);
    assert.deepEqual(pickOptions(decls, {source_root: {alpha: "//a/2"}}, "ALPHA"), {source_root: "//a/2"});
    assert.deepEqual(pickOptions(decls, {source_root: {alpha: "//a/2"}}, "BETA"), {});
});
