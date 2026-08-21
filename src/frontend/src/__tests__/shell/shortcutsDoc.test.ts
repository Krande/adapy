import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
// @ts-expect-error — plain-JS generator, no types, deliberately dependency-free.
import {DOC, REGISTRY, renderShortcutsDoc} from "../../../scripts/gen-shortcuts-doc.mjs";

// docs/SHORTCUTS.md must match the registry it is generated from.
//
// A generated file that is committed but never checked is a hand-written file with extra
// steps: it goes stale exactly as fast, and more quietly, because everyone assumes the
// generator ran. This one had already drifted — "Unhide all" was renamed to "Show all" in
// the registry months before anyone regenerated the reference, so the published shortcut
// list promised a command by a name the product no longer used.
//
// The test regenerates in memory and compares. It never writes: a test that fixes the
// thing it is checking reports success forever and tells you nothing.

describe("the shortcuts reference is current", () => {
    test("regenerating produces exactly the committed file", () => {
        const expected = renderShortcutsDoc(fs.readFileSync(REGISTRY, "utf8")).text;
        const actual = fs.readFileSync(DOC, "utf8").replace(/\r\n/g, "\n");
        assert.equal(
            actual,
            expected,
            "docs/SHORTCUTS.md is out of date — run `npm run gen:shortcuts` and commit it",
        );
    });

    test("the parse still finds shortcuts at all", () => {
        // The generator reads the registry as text. A refactor that reformats those object
        // literals — prettier widening the line, say — matches nothing, and an empty
        // reference would otherwise sail through the comparison above by matching an
        // equally empty regeneration.
        const {entries} = renderShortcutsDoc(fs.readFileSync(REGISTRY, "utf8"));
        assert.ok(entries.length > 10, `parsed only ${entries.length} shortcuts`);
    });

    test("every documented key appears in the rendered table", () => {
        const {entries, text} = renderShortcutsDoc(fs.readFileSync(REGISTRY, "utf8"));
        for (const e of entries) {
            assert.ok(text.includes(`\`${e.keys}\``), `${e.id} (${e.keys}) is missing from the doc`);
            assert.ok(text.includes(e.label), `${e.id}'s label is missing from the doc`);
        }
    });
});
