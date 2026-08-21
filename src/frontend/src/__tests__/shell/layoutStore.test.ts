import assert from "node:assert/strict";
import {test, beforeEach} from "node:test";

import {clampSize, defaultLayout, useLayoutStore} from "../../shell/layoutStore";
import {DOCK_LIMITS} from "../../shell/regions";
import {MODE_IDS} from "../../shell/modeStore";

const reset = () => useLayoutStore.getState().resetAll();
beforeEach(reset);

const layout = (mode: Parameters<typeof defaultLayout>[0]) => useLayoutStore.getState().perMode[mode];

test("every mode gets a complete default layout", () => {
    for (const mode of MODE_IDS) {
        const l = layout(mode);
        assert.ok(l, `${mode}: no layout`);
        for (const dock of ["left", "right", "bottom"] as const) {
            assert.ok(l.docks[dock], `${mode}/${dock}: missing`);
            assert.equal(typeof l.docks[dock].size, "number");
            // active must name a tab that actually exists, or be null.
            const {active, tabs} = l.docks[dock];
            if (active != null) assert.ok(tabs.includes(active), `${mode}/${dock}: active tab not in tabs`);
        }
    }
});

test("defaults are sparse — a mode does not open everything it could", () => {
    // "Too much on screen at once" is the problem being solved. A default that fills
    // every dock reproduces it.
    for (const mode of MODE_IDS) {
        const open = Object.values(layout(mode).docks).flatMap((d) => (d.collapsed ? [] : d.tabs));
        assert.ok(open.length <= 3, `${mode} opens ${open.length} panels by default`);
    }
});

test("dock sizes clamp to their limits", () => {
    assert.equal(clampSize("left", 5), DOCK_LIMITS.left.min);
    assert.equal(clampSize("left", 99999), DOCK_LIMITS.left.max);
    assert.equal(clampSize("left", 300), 300);

    useLayoutStore.getState().setDockSize("inspect", "left", 99999);
    assert.equal(layout("inspect").docks.left.size, DOCK_LIMITS.left.max);
});

test("opening a panel puts it in one place only", () => {
    const s = useLayoutStore.getState();
    s.openPanel("inspect", "scene", "right");
    s.openPanel("inspect", "scene", "left");

    const l = layout("inspect");
    const places = (["left", "right", "bottom"] as const).filter((d) => l.docks[d].tabs.includes("scene"));
    assert.deepEqual(places, ["left"], "a panel must not be open in two docks at once");
    assert.ok(!("scene" in l.floats));
});

test("opening a panel activates it and expands its dock", () => {
    const s = useLayoutStore.getState();
    s.toggleDock("inspect", "right", true);
    s.openPanel("inspect", "scene", "right");

    const d = layout("inspect").docks.right;
    assert.equal(d.active, "scene");
    assert.equal(d.collapsed, false, "opening a panel into a collapsed dock must reveal it");
});

test("closing the active tab promotes another rather than leaving a dangling id", () => {
    const s = useLayoutStore.getState();
    s.openPanel("inspect", "scene", "right");
    const before = layout("inspect").docks.right.tabs;
    assert.ok(before.length > 1, "precondition: more than one tab");

    s.closePanel("inspect", before[before.length - 1]);
    const d = layout("inspect").docks.right;
    assert.ok(!d.tabs.includes(before[before.length - 1]));
    if (d.tabs.length > 0) assert.ok(d.tabs.includes(d.active!), "active must still be a real tab");
    else assert.equal(d.active, null);
});

test("togglePanel is symmetric", () => {
    const s = useLayoutStore.getState();
    const isOpen = () => Object.values(layout("build").docks).some((d) => d.tabs.includes("scene"));

    s.togglePanel("build", "scene", "right");
    assert.equal(isOpen(), true);
    s.togglePanel("build", "scene", "right");
    assert.equal(isOpen(), false);
});

test("floating a docked panel removes it from its dock", () => {
    const s = useLayoutStore.getState();
    s.openPanel("inspect", "scene", "right");
    s.floatPanel("inspect", "scene", {x: 10, y: 10, w: 300, h: 300});

    const l = layout("inspect");
    assert.ok("scene" in l.floats);
    assert.ok(!Object.values(l.docks).some((d) => d.tabs.includes("scene")));
});

test("docking a floating panel removes it from the float layer", () => {
    const s = useLayoutStore.getState();
    s.floatPanel("inspect", "properties", {x: 10, y: 10, w: 300, h: 300});
    s.dockPanel("inspect", "properties", "right");

    const l = layout("inspect");
    assert.ok(!("properties" in l.floats));
    assert.ok(l.docks.right.tabs.includes("properties"));
});

test("layouts are per mode — editing one leaves the others alone", () => {
    // The whole justification for per-mode layouts: Results wants a wide bottom dock,
    // Build wants a tall right dock, and neither should disturb the other.
    const s = useLayoutStore.getState();
    const buildBefore = JSON.stringify(layout("build"));

    s.setDockSize("inspect", "left", 420);
    s.openPanel("inspect", "scene", "left");

    assert.equal(JSON.stringify(layout("build")), buildBefore);
    assert.equal(layout("inspect").docks.left.size, 420);
});

test("pinning is per mode and toggles", () => {
    const s = useLayoutStore.getState();
    s.togglePin("inspect", "outliner");
    assert.deepEqual(layout("inspect").pinned, ["outliner"]);
    assert.deepEqual(layout("results").pinned, []);
    s.togglePin("inspect", "outliner");
    assert.deepEqual(layout("inspect").pinned, []);
});

test("resetMode restores that mode's defaults only", () => {
    const s = useLayoutStore.getState();
    s.setDockSize("inspect", "left", 420);
    s.setDockSize("results", "right", 500);

    s.resetMode("inspect");
    assert.equal(layout("inspect").docks.left.size, DOCK_LIMITS.left.default);
    assert.equal(layout("results").docks.right.size, 500);
});

test("workspaces round-trip a full arrangement", () => {
    const s = useLayoutStore.getState();
    s.setDockSize("inspect", "left", 400);
    s.saveWorkspace("wide");

    s.setDockSize("inspect", "left", 200);
    assert.equal(layout("inspect").docks.left.size, 200);

    s.loadWorkspace("wide");
    assert.equal(layout("inspect").docks.left.size, 400);

    s.deleteWorkspace("wide");
    assert.equal(useLayoutStore.getState().workspaces.wide, undefined);
});

test("loading an unknown workspace is a no-op, not a wipe", () => {
    const s = useLayoutStore.getState();
    s.setDockSize("inspect", "left", 400);
    s.loadWorkspace("does-not-exist");
    assert.equal(layout("inspect").docks.left.size, 400);
});

test("toggling a panel in a COLLAPSED dock reveals it instead of removing it", () => {
    // Regression. Results mode ships fea-table in a collapsed bottom dock, so the panel
    // is present but invisible. Counting that as "open" made the rail button look
    // broken: the first click silently dropped a panel the user could not see, and only
    // a second click brought it back.
    const s = useLayoutStore.getState();
    s.openPanel("results", "fea-table", "bottom");
    s.toggleDock("results", "bottom", true);
    assert.equal(layout("results").docks.bottom.collapsed, true);

    s.togglePanel("results", "fea-table", "bottom");

    const d = layout("results").docks.bottom;
    assert.equal(d.collapsed, false, "the dock must expand");
    assert.ok(d.tabs.includes("fea-table"), "the panel must still be there");
    assert.equal(d.active, "fea-table");
});

test("toggling a VISIBLE panel still closes it", () => {
    const s = useLayoutStore.getState();
    s.openPanel("results", "fea-table", "bottom");
    assert.equal(layout("results").docks.bottom.collapsed, false);

    s.togglePanel("results", "fea-table", "bottom");
    assert.ok(!layout("results").docks.bottom.tabs.includes("fea-table"));
});

test("Build mode widens the right dock for the Builder", () => {
    // The Builder is a dense authoring surface — tabs, numeric fields, a compile
    // control. At the 300px default its content truncates, which reads as a broken
    // panel rather than a narrow one.
    const build = layout("build").docks.right;
    const inspect = layout("inspect").docks.right;
    assert.ok(build.size > inspect.size, "Build's right dock should be wider than Inspect's");
    assert.ok(build.tabs.includes("cellbuilder"));
    assert.equal(build.active, "cellbuilder", "authoring is what the mode is for");
});

test("Build mode ships the procedure graph collapsed", () => {
    // A second authoring surface, not something that should eat viewport height before
    // you ask for it.
    const bottom = layout("build").docks.bottom;
    assert.ok(bottom.tabs.includes("node-editor"));
    assert.equal(bottom.collapsed, true);
});

test("per-mode dock sizes are clamped like any other", () => {
    // defaultLayout's size override goes through the same clamp as a user drag, so a
    // future mode cannot ship a dock outside its own limits.
    for (const mode of MODE_IDS) {
        for (const dock of ["left", "right", "bottom"] as const) {
            const {size} = layout(mode).docks[dock];
            assert.ok(size >= DOCK_LIMITS[dock].min, `${mode}/${dock} below min`);
            assert.ok(size <= DOCK_LIMITS[dock].max, `${mode}/${dock} above max`);
        }
    }
});

test("no mode opens the file browser by default", () => {
    // There is no Library mode. Browsing files is something you do briefly DURING another
    // activity, so the Files panel toggles from the rail rather than being a place you
    // navigate to — and it does not occupy a dock until you ask for it.
    for (const mode of MODE_IDS) {
        // Convert is the exception, and earns it: you convert a file you can see, and
        // arriving in that mode without the source list would mean toggling a panel
        // before you could do the one thing the mode is for.
        const l = layout(mode);
        const open = Object.values(l.docks).flatMap((d) => (d.collapsed ? [] : d.tabs));
        assert.ok(!open.includes("convert"), `${mode} opens the converter as a dock panel`);
    }
});

test("Convert mode docks nothing — the converter is the main area", () => {
    // The converter is painted over the (still-mounted) canvas by AppShell, and its
    // source list is the Files flyout, which is not a dock panel either. A drop zone, a
    // target matrix and a job list stacked vertically never fit a sidebar.
    const l = layout("convert");
    assert.equal(l.docks.right.tabs.length, 0);
    assert.equal(l.docks.left.tabs.length, 0);
});

// --- adopting panels registered after a layout was saved -------------------------------
//
// The failure this guards is silent: a persisted layout is a closed list of panel ids, so
// a newly registered panel is in the registry and in the menu but on nobody's screen. It
// reads as the feature not shipping, and it happened once already.

import {adoptNewPanels, defaultPlacedPanels} from "../../shell/layoutStore";
import type {PanelId} from "../../shell/panelRegistry";

/** A saved layout that has never heard of `absent`. */
const layoutWithout = (mode: Parameters<typeof defaultLayout>[0], absent: PanelId) => {
    const l = defaultLayout(mode);
    for (const d of Object.values(l.docks)) {
        d.tabs = d.tabs.filter((t) => t !== absent);
        if (d.active === absent) d.active = d.tabs[0] ?? null;
    }
    return l;
};

const allTabs = (l: ReturnType<typeof defaultLayout>) => Object.values(l.docks).flatMap((d) => d.tabs);

test("a panel the mode's default layout wants, missing from a saved layout, gets placed", () => {
    for (const mode of MODE_IDS) {
        const wanted = allTabs(defaultLayout(mode));
        for (const panel of wanted) {
            const l = layoutWithout(mode, panel);
            adoptNewPanels(l, mode);
            assert.ok(allTabs(l).includes(panel), `${panel} should be adopted back in ${mode}`);
        }
    }
});

test("adoption never overrides a mode that deliberately leaves a panel out", () => {
    // The bug this caught: "outliner" is registered modes:"all" + defaultOpen, but Build's
    // default layout omits it on purpose -- the components tree already answers that
    // question. Adopting on the registry flag shoved a second tree into Build for every
    // user, on every load.
    for (const mode of MODE_IDS) {
        const omitted = MODE_IDS.flatMap((m) => allTabs(defaultLayout(m))).filter(
            (id) => !allTabs(defaultLayout(mode)).includes(id),
        );
        const l = defaultLayout(mode);
        adoptNewPanels(l, mode);
        for (const id of omitted) {
            assert.ok(!allTabs(l).includes(id), `${id} is not part of ${mode} and must stay out`);
        }
    }
});

test("adoption is idempotent, so a panel is never doubled", () => {
    for (const mode of MODE_IDS) {
        const l = defaultLayout(mode);
        const before = JSON.stringify(l);
        adoptNewPanels(l, mode);
        adoptNewPanels(l, mode);
        assert.equal(JSON.stringify(l), before, `${mode} already has its default panels`);
    }
});

test("adoption appends, and does not steal the active tab", () => {
    for (const mode of MODE_IDS) {
        for (const panel of allTabs(defaultLayout(mode))) {
            const l = layoutWithout(mode, panel);
            const docks = Object.entries(l.docks) as [keyof typeof l.docks, (typeof l.docks)[keyof typeof l.docks]][];
            const before = docks.map(([id, d]) => [id, [...d.tabs], d.active] as const);
            adoptNewPanels(l, mode);
            for (const [id, tabs, active] of before) {
                assert.deepEqual(l.docks[id].tabs.slice(0, tabs.length), tabs, "existing tabs keep their order");
                if (active !== null) assert.equal(l.docks[id].active, active, "the visible tab is not replaced");
            }
        }
    }
});

test("a panel the user closed stays closed once it is known", () => {
    // The distinction adoption could not previously make: "closed" and "never existed"
    // are both just absence in a saved layout. `known` records what has been offered, so
    // a deliberate close survives the next load instead of being undone.
    for (const mode of MODE_IDS) {
        for (const panel of allTabs(defaultLayout(mode))) {
            const l = layoutWithout(mode, panel);
            adoptNewPanels(l, mode, new Set([panel]));
            assert.ok(!allTabs(l).includes(panel), `${panel} was closed in ${mode} and must stay closed`);
        }
    }
});

test("a genuinely new panel is adopted even when others are known", () => {
    for (const mode of MODE_IDS) {
        const wanted = allTabs(defaultLayout(mode));
        if (wanted.length < 2) continue;
        const [fresh, ...rest] = wanted;
        const l = defaultLayout(mode);
        for (const d of Object.values(l.docks)) {
            d.tabs = d.tabs.filter((t) => t !== fresh);
            if (d.active === fresh) d.active = d.tabs[0] ?? null;
        }
        adoptNewPanels(l, mode, new Set(rest));
        assert.ok(allTabs(l).includes(fresh), `${fresh} is new in ${mode} and must be placed`);
    }
});

test("a fresh install has nothing to adopt", () => {
    // known is seeded with every default-placed panel, so a new user is never shown a
    // panel "arriving" that was simply always part of the layout.
    const known = new Set(defaultPlacedPanels());
    for (const mode of MODE_IDS) {
        const l = defaultLayout(mode);
        const before = JSON.stringify(l);
        adoptNewPanels(l, mode, known);
        assert.equal(JSON.stringify(l), before);
    }
});
