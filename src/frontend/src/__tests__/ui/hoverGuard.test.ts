import {test, describe} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Every hover style goes through `pointer-fine:`.
//
// Plain `hover:` sticks on touch devices: tapping fires :hover, and the highlight stays
// lit until you tap something else. On a phone that leaves a trail of controls that all
// look focused, and the one you actually pressed is indistinguishable from the four you
// scrolled past.
//
// The guard is baked into the Button family so no call site has to remember it — which is
// exactly why it can rot unnoticed: nobody writing a panel ever thinks about it, and a
// primitive that quietly drops the prefix looks identical on the desktop where it is
// written. The failure only appears on hardware the author is not holding.
//
// Scanned as source text because the rule is about the class NAME. Tailwind resolves
// `pointer-fine:hover:bg-x` and `hover:bg-x` to different CSS, and no render test in jsdom
// distinguishes them.

const ROOT = path.resolve(import.meta.dirname, "../..");
// Every directory that draws, not just the new code. The guard started scoped to
// components/ui and shell because 73 older files still used a bare hover:; those were
// converted in one mechanical pass (326 sites), so there is no allowlist and no
// burn-down — the rule simply holds everywhere.
const ROOTS = ["components", "shell", "plugins"].map((d) => path.resolve(ROOT, d));

function walk(dir: string, out: string[] = []): string[] {
    for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
            if (e.name === "__gallery__") continue;
            walk(full, out);
        } else if (/\.tsx?$/.test(e.name)) {
            out.push(full);
        }
    }
    return out;
}

const stripComments = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

/**
 * A `hover:` that starts its own variant chain.
 *
 * Excluding `:` as well as word characters is not laziness — it exempts `disabled:hover:`
 * and `sm:hover:`, and both occurrences of those in this codebase are hover *resets*
 * (`disabled:hover:bg-transparent`, `sm:hover:bg-transparent`). Forcing `pointer-fine:`
 * onto a reset would stop it applying on touch, which is the one place the sticky
 * highlight it cancels actually happens: the guard would have caused the bug it exists to
 * prevent.
 *
 * This is the same pattern the codemod used, deliberately. A report that disagrees with
 * its gate is how the ui-audit came to claim 82 offending files while the test found one.
 */
const BARE_HOVER = /(?<![\w:-])hover:/;

function offenders(): {file: string; line: number; text: string}[] {
    const bad: {file: string; line: number; text: string}[] = [];
    for (const dir of ROOTS) {
        for (const file of walk(dir)) {
            const lines = stripComments(fs.readFileSync(file, "utf8")).split("\n");
            lines.forEach((l, i) => {
                if (BARE_HOVER.test(l)) {
                    bad.push({
                        file: path.relative(ROOT, file).split(path.sep).join("/"),
                        line: i + 1,
                        text: l.trim().slice(0, 90),
                    });
                }
            });
        }
    }
    return bad;
}

describe("sticky-hover guard", () => {
    test("nothing under components, shell or plugins uses a bare hover:", () => {
        const bad = offenders();
        assert.deepEqual(
            bad.map((b) => `${b.file}:${b.line}  ${b.text}`),
            [],
            "use pointer-fine:hover: — a bare hover: sticks after a tap on touch devices",
        );
    });

    test("the pattern actually catches a bare hover", () => {
        // Guarding the guard. A regex that matches nothing passes the test above forever
        // and means nothing — the same way the noNativeDialogs pattern reported clean
        // files while six offenders sat in them.
        assert.ok(BARE_HOVER.test('className="hover:bg-surface-2"'));
        assert.ok(BARE_HOVER.test("hover:text-content"));
    });

    test("and lets the guarded form through", () => {
        assert.ok(!BARE_HOVER.test('className="pointer-fine:hover:bg-surface-2"'));
    });

    test("a chained variant is left alone", () => {
        // These cancel a hover style. Guarding them would stop the cancellation applying
        // on touch — exactly where the sticky highlight they exist to kill occurs.
        assert.ok(!BARE_HOVER.test("disabled:hover:bg-transparent"));
        assert.ok(!BARE_HOVER.test("sm:hover:bg-transparent"));
        assert.ok(!BARE_HOVER.test("group-hover:opacity-100"));
    });

    test("the Button family really carries the guard", () => {
        // The whole point of baking it into the primitives: if this is ever true and the
        // test above is also true, the guard was removed rather than moved.
        const button = fs.readFileSync(path.join(ROOT, "components/ui/Button.tsx"), "utf8");
        assert.match(button, /pointer-fine:hover:/, "Button no longer guards its hover styles");
    });
});
