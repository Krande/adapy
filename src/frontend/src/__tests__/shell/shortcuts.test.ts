import assert from "node:assert/strict";
import {test} from "node:test";
import fs from "node:fs";
import path from "node:path";

import {SHORTCUTS, globalBindings, keysFor, shortcutFor} from "../../shell/shortcuts";

// Keeps the shortcut registry honest against the code that actually binds keys.
//
// The registry feeds the command palette, rail tooltips and docs/SHORTCUTS.md. All three
// are documentation, and documentation drifts SILENTLY — a tooltip promising a key that
// was renamed is wrong forever and nothing notices. So the global entries are checked
// against setupCameraControlsHandlers by parsing it: add or remove a binding there
// without updating the registry and this fails.

const HANDLER = path.resolve(
    import.meta.dirname,
    "../../components/viewer/sceneHelpers/setupCameraControlsHandlers.ts",
);

/**
 * Extract `shift && key === "x"` pairs from the real handler.
 *
 * Deliberately a parse of the source rather than a mock of the module: importing it would
 * drag in three.js and every store, and the thing worth checking is the literal list of
 * keys a reader would see.
 */
function bindingsInHandler(): {shift: boolean; key: string}[] {
    const src = fs.readFileSync(HANDLER, "utf8");
    const out: {shift: boolean; key: string}[] = [];
    for (const m of src.matchAll(/(shift\s*&&\s*)?key\s*===\s*"([a-z]+)"/g)) {
        out.push({shift: Boolean(m[1]), key: m[2].replace(/^arrow/, "")});
    }
    return out;
}

const norm = (b: {shift: boolean; key: string}) => `${b.shift ? "shift+" : ""}${b.key}`;

test("every global shortcut in the registry is actually bound", () => {
    const bound = new Set(bindingsInHandler().map(norm));
    const missing = globalBindings()
        .map(norm)
        .filter((k) => !bound.has(k));
    assert.deepEqual(
        missing,
        [],
        `The registry promises these but setupCameraControlsHandlers does not bind them:\n  ${missing.join("\n  ")}`,
    );
});

test("every bound global key is described in the registry", () => {
    // The other direction: a binding nobody documented is undiscoverable, which is the
    // problem this milestone exists to fix.
    const described = new Set(globalBindings().map(norm));
    const undocumented = [...new Set(bindingsInHandler().map(norm))].filter((k) => !described.has(k));
    assert.deepEqual(
        undocumented,
        [],
        `These keys are bound but appear in no reference:\n  ${undocumented.join("\n  ")}`,
    );
});

test("shortcut ids are unique", () => {
    const ids = SHORTCUTS.map((s) => s.id);
    assert.equal(new Set(ids).size, ids.length);
});

test("no two shortcuts in the same scope claim the same keys", () => {
    // Across scopes is fine and intended — Shift+H hides ranges globally and cells in the
    // builder, which is one concept with two implementations. Within a scope it is a bug.
    const seen = new Map<string, string>();
    for (const s of SHORTCUTS) {
        const key = `${s.scope}:${s.keys.toLowerCase()}`;
        const prev = seen.get(key);
        assert.equal(prev, undefined, `${s.keys} is claimed by both "${prev}" and "${s.id}" in scope ${s.scope}`);
        seen.set(key, s.id);
    }
});

test("every shortcut is described well enough to be listed", () => {
    for (const s of SHORTCUTS) {
        assert.ok(s.label.trim().length > 3, `${s.id}: label too thin`);
        assert.ok(s.group.trim().length > 0, `${s.id}: no group`);
        assert.match(s.keys, /^[A-Za-z0-9+]+$/, `${s.id}: keys should be a plain combo, got "${s.keys}"`);
    }
});

test("lookups behave for known and unknown ids", () => {
    assert.equal(keysFor("fit-all"), "Shift+A");
    assert.equal(keysFor("no-such-shortcut"), undefined);
    assert.equal(shortcutFor("no-such-shortcut"), undefined);
});

test("builder shortcuts are tool-scoped, never mode-scoped", () => {
    // The non-modality contract: G/R/S and X/Y/Z key off cellBuilderStore.active, not off
    // the active mode. Marking them "global" here would be a lie; marking them with a
    // mode would encode exactly the coupling the contract forbids.
    const builder = SHORTCUTS.filter((s) => ["gizmo-move", "gizmo-rotate", "gizmo-scale"].includes(s.id));
    assert.equal(builder.length, 3);
    for (const s of builder) assert.equal(s.scope, "builder");
});
