import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Native window.confirm / prompt / alert are banned outside the allowlist.
//
// They are blocking, unstyleable, and visibly not part of the application: a browser
// dialog over a dark themed viewer reads as a different program. In the embed build it
// is worse — the dialog carries the HOST page's origin, so a docs page shows
// "docs.example.com says: Delete file?", which looks like a phishing attempt.
//
// `confirm()` / `promptText()` / `alertText()` from @/ui/confirm are the replacements;
// they render through ConfirmHost and are awaited exactly the same way.
//
// The allowlist is a burn-down, same as noAdHocChrome's. It shrinks; it never grows.

const ROOT = path.resolve(import.meta.dirname, "../..");

// Empty. It got there — the admin tabs were the last seven.
const ALLOW = new Set<string>([]);

function walk(dir: string, out: string[] = []): string[] {
    for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
            if (e.name === "__tests__" || e.name === "node_modules") continue;
            walk(full, out);
        } else if (/\.tsx?$/.test(e.name)) {
            out.push(full);
        }
    }
    return out;
}

/**
 * Calls only — a comment saying "replaces the old window.prompt flow" is not a call.
 *
 * Both spellings. `window.confirm(…)` is the obvious one; a bare `confirm(…)` is the same
 * global opening the same dialog, and matching only the qualified form let six of them sit
 * in the admin tabs while this test reported those files clean. A rule that catches only
 * the spelling people happened to use is not a rule.
 *
 * The lookbehind keeps member calls (`x.alert(`) out; `EXEMPT` below keeps our own
 * replacements out.
 */
const CALL = /(?<![\w.])(?:window\.)?(confirm|prompt|alert)\s*\(/;

/** Our own dialogs. Native ones are never awaited — they block. */
const EXEMPT = /(await\s+(confirm|promptText|alertText)\s*\()|(\b(alertText|promptText)\s*\()/;

function offenders(): string[] {
    const bad: string[] = [];
    for (const file of walk(path.join(ROOT, "components")).concat(walk(path.join(ROOT, "shell")))) {
        const rel = path.relative(ROOT, file).split(path.sep).join("/");
        const lines = fs.readFileSync(file, "utf8").split("\n");
        const hit = lines.some((l) => {
            const t = l.trimStart();
            if (t.startsWith("//") || t.startsWith("*") || t.startsWith("{/*")) return false;
            if (EXEMPT.test(t)) return false;
            return CALL.test(t);
        });
        if (hit) bad.push(rel);
    }
    return bad;
}

describe("no native browser dialogs", () => {
    test("only allowlisted files still call window.confirm/prompt/alert", () => {
        const bad = offenders().filter((f) => !ALLOW.has(f));
        assert.deepEqual(bad, [], `use @/ui/confirm instead of a native dialog in:\n  ${bad.join("\n  ")}`);
    });

    test("the allowlist has no stale entries", () => {
        // A burn-down list that keeps names of already-converted files stops being a
        // measure of anything, and quietly re-permits what it names.
        const current = new Set(offenders());
        const stale = [...ALLOW].filter((f) => !current.has(f));
        assert.deepEqual(stale, [], `already converted — drop from the allowlist:\n  ${stale.join("\n  ")}`);
    });

    test("a bare confirm() counts, not just window.confirm()", () => {
        // The case that caught this test out: both spellings call the same global and
        // open the same dialog, and matching only the qualified one left six of them in
        // the admin tabs while those files reported clean.
        assert.match('if (!confirm("really?")) return;', CALL);
        assert.match('window.confirm("really?")', CALL);
    });

    test("our own awaited dialogs are not mistaken for the ones they replace", () => {
        assert.ok(EXEMPT.test("const ok = await confirm({"));
        assert.ok(EXEMPT.test("void alertText({"));
        assert.ok(!EXEMPT.test('if (!confirm("really?")) return;'));
    });

    test("member calls are not swept up", () => {
        // `useConfirmStore.getState().confirm(` and the like are ours.
        assert.ok(!CALL.test("store.confirm(x)"));
    });
});
