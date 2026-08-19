import assert from "node:assert/strict";
import {test} from "node:test";

import {
    ALL_PANELS,
    PANEL_IDS,
    PANELS,
    isPanelId,
    panelsForMode,
    resolvePanel,
} from "../../shell/panelRegistry";
import {MODE_IDS} from "../../shell/modeStore";
import {DOCK_IDS} from "../../shell/regions";

// The mechanical half of the parity guarantee.
//
// docs/FEATURE_INVENTORY.md is the human checklist; this is the part CI can enforce. A
// panel that disappears during the rewrite — deleted, renamed, or dropped from every
// mode by a bad edit — fails here instead of quietly vanishing from the product.
//
// EXPECTED_PANELS is written out rather than derived from PANEL_IDS on purpose: deriving
// it would make the test pass no matter what the registry contained.

const EXPECTED_PANELS = ["outliner", "properties", "scene", "simulation", "fea-table", "cellbuilder", "node-editor", "storage", "convert", "admin", "preferences"];

test("every expected panel is registered", () => {
    for (const id of EXPECTED_PANELS) {
        assert.ok(isPanelId(id), `panel "${id}" is no longer a known id`);
        assert.ok(PANELS[id as never], `panel "${id}" is missing from the registry`);
    }
});

test("no panel was added without updating the inventory", () => {
    // Catches the other direction: a new panel must be recorded, so the inventory and
    // the registry cannot drift.
    assert.deepEqual([...PANEL_IDS].sort(), [...EXPECTED_PANELS].sort());
});

test("panel ids are unique", () => {
    assert.equal(new Set(PANEL_IDS).size, PANEL_IDS.length);
    // The key and the id must agree — they are used interchangeably by layout state.
    for (const [key, def] of Object.entries(PANELS)) assert.equal(key, def.id);
});

test("every panel has the fields the shell needs to render it", () => {
    for (const def of ALL_PANELS) {
        assert.ok(def.title.trim().length > 0, `${def.id}: empty title`);
        assert.ok(def.icon, `${def.id}: no icon`);
        assert.ok(def.component, `${def.id}: no component`);
        assert.ok(
            (DOCK_IDS as readonly string[]).includes(def.defaultDock),
            `${def.id}: unknown defaultDock "${def.defaultDock}"`,
        );
    }
});

test("every panel is reachable from at least one mode", () => {
    // A panel offered by no mode is unreachable in the UI — the modern equivalent of
    // the old dead toggles.
    for (const def of ALL_PANELS) {
        if (def.modes === "all") continue;
        assert.ok(def.modes.length > 0, `${def.id} is offered by no mode`);
        for (const m of def.modes) {
            assert.ok(MODE_IDS.includes(m), `${def.id} names unknown mode "${m}"`);
        }
    }
});

test("every mode offers at least one panel", () => {
    // Availability predicates read window globals; stub REST off so this runs in node.
    (globalThis as Record<string, unknown>).window = {};
    for (const mode of MODE_IDS) {
        assert.ok(panelsForMode(mode).length > 0, `mode "${mode}" offers no panels`);
    }
});

test("mode-independent panels are offered in every mode", () => {
    (globalThis as Record<string, unknown>).window = {};
    // Non-modality: the outliner and the properties panel must never be gated by mode.
    for (const mode of MODE_IDS) {
        const ids = panelsForMode(mode).map((p) => p.id);
        assert.ok(ids.includes("outliner"), `outliner missing from "${mode}"`);
        assert.ok(ids.includes("properties"), `properties missing from "${mode}"`);
    }
});

test("resolvePanel rejects ids that a stale persisted layout might carry", () => {
    assert.equal(resolvePanel("no-such-panel"), null);
    assert.equal(resolvePanel(""), null);
    (globalThis as Record<string, unknown>).window = {};
    assert.equal(resolvePanel("outliner")?.id, "outliner");
});

test("runtime-gated panels resolve to null when unavailable", () => {
    // Storage is REST-only. In a desktop/WS build it must degrade to an empty dock
    // rather than mounting a panel whose API does not exist.
    (globalThis as Record<string, unknown>).window = {COMMS_MODE: "ws"};
    assert.equal(resolvePanel("storage"), null);
    assert.ok(!panelsForMode("data").some((p) => p.id === "storage"));

    (globalThis as Record<string, unknown>).window = {COMMS_MODE: "rest"};
    assert.equal(resolvePanel("storage")?.id, "storage");
    assert.ok(panelsForMode("data").some((p) => p.id === "storage"));
});
