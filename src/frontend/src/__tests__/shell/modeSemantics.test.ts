import assert from "node:assert/strict";
import {test} from "node:test";
import fs from "node:fs";
import path from "node:path";

import {MODES, MODE_IDS, isModeId, modeDef, useModeStore} from "../../shell/modeStore";

// The non-modality contract, as an executable spec.
//
// The prose version lives at the top of shell/modeStore.ts. This is the part that fails
// the build when someone "helpfully" makes a mode switch also clear the selection, or
// auto-jumps to Results when a deck loads.
//
// Two complementary guarantees:
//   1. BEHAVIOURAL — setMode changes the mode and nothing else on this store.
//   2. STRUCTURAL — modeStore may not even IMPORT a viewer/business store. That is the
//      stronger claim: you cannot mutate what you cannot reach, and unlike a snapshot
//      test it cannot be defeated by a mutation that happens to restore its own value.

const reset = () => useModeStore.setState({mode: "inspect", badges: {}});

test("setMode changes only the mode", () => {
    reset();
    const before = useModeStore.getState();
    useModeStore.getState().setMode("build");
    const after = useModeStore.getState();

    assert.equal(after.mode, "build");
    // Every other field is untouched (badges is empty in both, and identity is
    // preserved when nothing was cleared).
    assert.deepEqual(after.badges, before.badges);
});

test("setMode to the current mode is a no-op", () => {
    reset();
    const before = useModeStore.getState();
    useModeStore.getState().setMode("inspect");
    // Same object identity: no spurious re-render for subscribers.
    assert.equal(useModeStore.getState(), before);
});

test("entering a mode clears only that mode's badge", () => {
    reset();
    useModeStore.getState().setBadge("results", "dot");
    useModeStore.getState().setBadge("convert", 3);

    useModeStore.getState().setMode("results");

    const {badges} = useModeStore.getState();
    assert.equal(badges.results, undefined, "the entered mode's badge is acknowledged");
    assert.equal(badges.convert, 3, "other modes' badges survive");
});

test("a badge NEVER changes the active mode", () => {
    // The most-violated Blender principle in DCC clones: loading data must not yank the
    // user somewhere else. A badge is a passive signal, nothing more.
    reset();
    useModeStore.getState().setBadge("results", "dot");
    assert.equal(useModeStore.getState().mode, "inspect");
    useModeStore.getState().setBadge("build", 7);
    assert.equal(useModeStore.getState().mode, "inspect");
});

test("modeStore imports nothing that owns viewer or business state", () => {
    // The structural guarantee. If modeStore cannot reach the scene, the selection, the
    // model or the cellbuilder, then setMode provably cannot disturb them.
    const src = fs.readFileSync(
        path.resolve(import.meta.dirname, "../../shell/modeStore.ts"),
        "utf8",
    );
    const imports = [...src.matchAll(/^\s*import\s[^;]*?from\s+["']([^"']+)["']/gm)].map((m) => m[1]);

    const forbidden = /state\/|utils\/scene|utils\/mesh_select|utils\/cellbuilder|services\/|three/;
    const bad = imports.filter((i) => forbidden.test(i));
    assert.deepEqual(
        bad,
        [],
        `modeStore must not import viewer/business modules — found: ${bad.join(", ")}`,
    );
});

test("every mode is fully described for the switcher", () => {
    assert.equal(MODES.length, MODE_IDS.length);
    for (const m of MODES) {
        assert.ok(isModeId(m.id));
        assert.ok(m.label.trim().length > 0, `${m.id}: no label`);
        assert.ok(m.icon.startsWith("mode-"), `${m.id}: icon should be a mode glyph`);
        // The hint says what you DO in the mode; it is the only in-product explanation
        // of what the four modes mean.
        assert.ok(m.hint.trim().length > 10, `${m.id}: hint is too thin to be useful`);
    }
    assert.equal(new Set(MODES.map((m) => m.id)).size, MODES.length, "duplicate mode ids");
});

test("modeDef falls back rather than throwing on an unknown id", () => {
    // Persisted state can name a mode that no longer exists; the shell must still boot.
    assert.equal(modeDef("nope" as never).id, "inspect");
});

test("the default mode is the one that works with nothing loaded", () => {
    // Inspect is the only mode that is useful on an empty scene, so it is the one a cold
    // start lands in.
    reset();
    assert.equal(useModeStore.getState().mode, "inspect");
});
