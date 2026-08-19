import assert from "node:assert/strict";
import {test} from "node:test";
import fs from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const ALLOWLIST: string[] = require("./adHocChrome.allowlist.json");

// The design-system burn-down, enforced.
//
// There is no ESLint in this repo (prettier only), so the test runner is the
// enforcement point. Any file under src/components/** that hardcodes a Tailwind
// palette colour must be on the allowlist; the allowlist only ever shrinks.
//
// The allowlist is a committed data file rather than a count so the diff shows
// exactly which files were converted — a bare count would let someone clean one file
// while dirtying another and call it progress.
//
// To convert a file: replace bg-gray-800 / bg-blue-600 / … with the semantic
// utilities (bg-surface-1, bg-surface-2, bg-accent, …) or, better, with a primitive
// from @/components/ui — then delete its line from adHocChrome.allowlist.json.

const COMPONENTS = path.resolve(import.meta.dirname, "../../components");
const PALETTE = /\bbg-(?:gray|blue|red|green|yellow|indigo|slate|zinc|neutral|stone)-[0-9]{2,3}\b/;

/**
 * Drop comments before scanning.
 *
 * Documenting what a class used to be ("this replaces the ad-hoc bg-blue-600 family")
 * is not a violation — and forbidding it would make the migration undocumentable.
 * Crude but sufficient: these are .tsx sources, and a false negative here only means
 * a class hidden inside a string that looks like a comment.
 */
const stripComments = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

function walk(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir)) {
        const full = path.join(dir, entry);
        if (fs.statSync(full).isDirectory()) {
            // The design system itself is where the palette is allowed to be named.
            if (entry !== "ui") walk(full, out);
            continue;
        }
        if (/\.tsx?$/.test(entry)) out.push(full);
    }
    return out;
}

const rel = (p: string) => path.relative(path.resolve(import.meta.dirname, "../../.."), p).split(path.sep).join("/");

const offenders = walk(COMPONENTS)
    .filter((f) => PALETTE.test(stripComments(fs.readFileSync(f, "utf8"))))
    .map(rel)
    .sort();

test("no NEW file hardcodes a palette colour", () => {
    const added = offenders.filter((f) => !ALLOWLIST.includes(f));
    assert.deepEqual(
        added,
        [],
        `These files hardcode Tailwind palette colours and are not on the allowlist.\n` +
            `Use the semantic utilities (bg-surface-*, text-content-*, bg-accent) or a\n` +
            `primitive from @/components/ui instead:\n  ${added.join("\n  ")}`,
    );
});

test("the allowlist only shrinks — remove entries as files are converted", () => {
    const stale = ALLOWLIST.filter((f) => !offenders.includes(f));
    assert.deepEqual(
        stale,
        [],
        `These files no longer hardcode palette colours (or no longer exist).\n` +
            `Delete them from adHocChrome.allowlist.json to bank the progress:\n  ${stale.join("\n  ")}`,
    );
});

test("the design system itself does not regress into ad-hoc colours", () => {
    const uiDir = path.join(COMPONENTS, "ui");
    const bad = walk(uiDir)
        .filter((f) => PALETTE.test(stripComments(fs.readFileSync(f, "utf8"))))
        .map(rel);
    assert.deepEqual(bad, [], `src/components/ui must use tokens only:\n  ${bad.join("\n  ")}`);
});
