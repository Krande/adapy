import assert from "node:assert/strict";
import {test} from "node:test";

import {
    LEGACY_REGION_PLACEMENT,
    PLUGIN_API_VERSION,
    isLegacyRegion,
    resolveSlotPlacement,
    // Exported under a test alias by the plugin framework; used here rather than
    // widening its public surface.
    _versionSatisfies as versionSatisfies,
} from "../../plugins/registry";
import fs from "node:fs";
import path from "node:path";
import {DOCK_IDS} from "../../shell/regions";
import {MODE_IDS} from "../../shell/modeStore";

// The plugin framework's stated principle is "core adapy carries NO hardcoded knowledge
// of any feature plugin". The shell has to extend it without breaking anything built
// against Phase 1 — so these tests pin the compatibility surface rather than the
// implementation.

test("the four original regions still exist", () => {
    // Plugins are built against these names. Removing one is a breaking change that
    // would need a major version, not a quiet edit.
    for (const r of ["fem-sidebar", "top-panel", "scene-info", "storage-detail"] as const) {
        assert.ok(isLegacyRegion(r), `legacy region "${r}" was removed`);
    }
});

test("every legacy region maps to a real dock and real modes", () => {
    for (const [region, placement] of Object.entries(LEGACY_REGION_PLACEMENT)) {
        assert.ok(
            (DOCK_IDS as readonly string[]).includes(placement.dock),
            `${region} maps to unknown dock "${placement.dock}"`,
        );
        if (placement.modes === null) continue;
        for (const m of placement.modes) {
            assert.ok((MODE_IDS as readonly string[]).includes(m), `${region} names unknown mode "${m}"`);
        }
    }
});

test("the two previously-unwired regions now have real hosts", () => {
    // scene-info and storage-detail were declared in Phase 1 and never consumed. The
    // shell finally gives them somewhere to render.
    assert.ok(LEGACY_REGION_PLACEMENT["scene-info"].dock);
    assert.ok(LEGACY_REGION_PLACEMENT["storage-detail"].dock);
});

test("a Phase-1 slot needs no changes to be placed", () => {
    // The whole compatibility story: region alone is enough.
    const fem = resolveSlotPlacement({region: "fem-sidebar"});
    assert.equal(fem.dock, "right");
    assert.deepEqual(fem.modes, ["results"], "the FEM sidebar belongs to Results");

    const top = resolveSlotPlacement({region: "top-panel"});
    assert.equal(top.modes, null, "top-bar contributions were mode-independent and stay so");
});

test("explicit dock/modes win over the legacy mapping", () => {
    const p = resolveSlotPlacement({region: "fem-sidebar", dock: "bottom", modes: ["build"]});
    assert.equal(p.dock, "bottom");
    assert.deepEqual(p.modes, ["build"]);
});

test("a slot naming a dock region directly resolves to itself", () => {
    const p = resolveSlotPlacement({region: "bottom"});
    assert.equal(p.dock, "bottom");
    assert.equal(p.modes, null, "unscoped means every mode");
});

test("API 1.1.0 still satisfies the range Phase-1 plugins declare", () => {
    // The demo plugin ships coreApiRange ">=1.0 <2.0". A plugin built before the shell
    // must keep loading.
    assert.equal(PLUGIN_API_VERSION, "1.1.0");
    assert.equal(versionSatisfies(PLUGIN_API_VERSION, ">=1.0 <2.0"), true);
    assert.equal(versionSatisfies(PLUGIN_API_VERSION, ">=1.1"), true);
    // And a plugin requiring a future API is still correctly rejected.
    assert.equal(versionSatisfies(PLUGIN_API_VERSION, ">=2.0"), false);
});

test("the shell hosts every plugin region that the classic UI hosted", () => {
    // Regression. Plugin `top-panel` contributions were hosted ONLY in Menu.tsx, which
    // the shell never renders — so enabling the shell silently dropped a plugin's
    // top-bar button (inventory row B11). This asserts each live region has a host
    // inside src/shell, so a future region cannot be added to the union without one.
    const shellDir = path.resolve(import.meta.dirname, "../../shell");
    const sources = fs
        .readdirSync(shellDir)
        .filter((f) => /\.tsx$/.test(f))
        .map((f) => fs.readFileSync(path.join(shellDir, f), "utf8"))
        .join("\n");

    assert.match(sources, /PluginTopBarButtons/, "no host for plugin top-bar buttons");
    assert.match(sources, /region="top-panel"/, "no host for the top-panel region");

    // fem-sidebar is hosted by SimulationControls, which the shell mounts as the
    // Results-mode Simulation panel — so it needs no host of its own here.
    const registry = fs.readFileSync(path.resolve(import.meta.dirname, "../../shell/panelRegistry.ts"), "utf8");
    assert.match(registry, /simulation\/SimulationControls/, "fem-sidebar's host is not mounted by the shell");
});

test("the shell performs the REST bootstrap the classic path does", () => {
    // Regression. AuthGate is not only a sign-in gate: it calls /api/me and populates the
    // scope store. The shell originally rendered AppShell without it, so it booted with
    // no scopes and no admin flag — the scope picker rendered nothing and every scoped
    // request fell back to a default. Nothing errored; it was simply wrong.
    const app = fs.readFileSync(path.resolve(import.meta.dirname, "../../app.tsx"), "utf8");
    const shellBranch = app.slice(app.indexOf("if (useNewShell)"), app.indexOf("if (isAuthCallback)"));
    assert.match(shellBranch, /AuthGate/, "the shell branch must mount AuthGate in REST mode");
    assert.match(shellBranch, /isRestMode/, "and must gate it on REST, as the classic path does");
});
