import assert from "node:assert/strict";
import {test} from "node:test";
import fs from "node:fs";
import path from "node:path";

import {Z, Z_ORDER} from "../../shell/zIndex";

// Keeps layering a decision made once.
//
// The old UI had no registry: z-10 top bar, z-20 tree, z-30/z-40 mobile sheets, z-50
// toasts, z-60 modal host, z-[70] shortcuts, and an inline z-index:1000 on the node
// editor — each chosen by picking a number bigger than whatever that author happened to
// know about. These tests stop the shell drifting back into that.

const ROOT = path.resolve(import.meta.dirname, "../..");

test("layers are strictly ordered with no ties", () => {
    const values = Z_ORDER.map((k) => Z[k]);
    for (let i = 1; i < values.length; i++) {
        assert.ok(values[i] > values[i - 1], `${Z_ORDER[i]} must sit above ${Z_ORDER[i - 1]}`);
    }
});

test("the ordering encodes the decisions that matter", () => {
    // A toast above a dialog: a job failure must be readable even while a modal is open.
    assert.ok(Z.toast > Z.dialog === false, "dialog sits above toast");
    assert.ok(Z.toast > Z.contextMenu, "a toast must not be hidden behind an open menu");
    // The canvas is the floor; everything is above it.
    assert.equal(Math.min(...Object.values(Z)), Z.canvas);
    // Docks above the canvas, floats above docks, menus above floats.
    assert.ok(Z.dock > Z.canvas);
    assert.ok(Z.float > Z.dock);
    assert.ok(Z.contextMenu > Z.float);
    // The drag ghost must be visible over whatever it is dragged across.
    assert.ok(Z.dragPreview > Z.dialog);
});

test("the CSS mirror agrees with the TS registry", () => {
    // tokens.css exposes --ada-z-* so plugin stylesheets and core components cannot
    // disagree about layering. Drift between the two is invisible until something
    // renders behind something else.
    const css = fs.readFileSync(path.join(ROOT, "ui/tokens.css"), "utf8");
    const kebab = (k: string) => k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

    for (const key of Z_ORDER) {
        const name = `--ada-z-${kebab(key)}`;
        const m = new RegExp(`${name}\\s*:\\s*(\\d+)`).exec(css);
        assert.ok(m, `${name} is missing from ui/tokens.css`);
        assert.equal(Number(m![1]), Z[key], `${name} disagrees with zIndex.ts`);
    }
});

test("the shell does not hand-roll arbitrary z-index values", () => {
    // Arbitrary Tailwind z values (`z-[1000]`) are exactly how the old layering spread.
    // Inside src/shell everything must come from the registry.
    const offenders: string[] = [];
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir)) {
            const full = path.join(dir, entry);
            if (fs.statSync(full).isDirectory()) {
                walk(full);
                continue;
            }
            if (!/\.tsx?$/.test(entry)) continue;
            if (entry === "zIndex.ts") continue;
            const src = fs.readFileSync(full, "utf8");
            if (/\bz-\[/.test(src) || /zIndex:\s*\d/.test(src)) {
                offenders.push(path.relative(ROOT, full).split(path.sep).join("/"));
            }
        }
    };
    walk(path.join(ROOT, "shell"));
    assert.deepEqual(offenders, [], `use Z from shell/zIndex.ts instead of a literal:\n  ${offenders.join("\n  ")}`);
});
