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
const ROOTS = ["components/ui", "shell"].map((d) => path.resolve(ROOT, d));

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

/** A `hover:` NOT preceded by `pointer-fine:` (or another variant chain ending in it). */
const BARE_HOVER = /(?<!pointer-fine:)(?<![\w-])hover:/;

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
    test("the design system and the shell never use a bare hover:", () => {
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

    test("the Button family really carries the guard", () => {
        // The whole point of baking it into the primitives: if this is ever true and the
        // test above is also true, the guard was removed rather than moved.
        const button = fs.readFileSync(path.join(ROOT, "components/ui/Button.tsx"), "utf8");
        assert.match(button, /pointer-fine:hover:/, "Button no longer guards its hover styles");
    });
});
