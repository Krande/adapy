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
    s.floatPanel("inspect", "preferences", {x: 10, y: 10, w: 300, h: 300});
    s.dockPanel("inspect", "preferences", "right");

    const l = layout("inspect");
    assert.ok(!("preferences" in l.floats));
    assert.ok(l.docks.right.tabs.includes("preferences"));
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

test("Library mode opens the file browser", () => {
    const l = layout("data");
    assert.ok(l.docks.left.tabs.includes("storage"));
    assert.equal(l.docks.left.collapsed, false);
    // Convert used to sit in this mode's right dock. It is its own mode now: converting
    // is a different activity from browsing, and sharing the dock made picking a file
    // and deciding what to do with it compete for one column.
    assert.ok(!l.docks.right.tabs.includes("convert"), "convert moved to its own mode");
});

test("Convert mode shows the files it acts on, and nothing in the right dock", () => {
    // The point of folding /convert into the shell at all: as a separate PAGE it was a
    // dead end — no way back to the viewer, and no sight of the storage it reads from.
    const l = layout("convert");
    assert.ok(l.docks.left.tabs.includes("storage"), "you convert a file you can see");
    assert.equal(l.docks.left.collapsed, false);
    // The converter is the MAIN AREA, painted over the (still-mounted) canvas by
    // AppShell — not a docked panel. It was one briefly, and a drop zone, a target
    // matrix and a job list stacked vertically never fit a sidebar.
    assert.ok(!l.docks.right.tabs.includes("convert"), "the converter is not a dock panel");
    assert.equal(l.docks.right.tabs.length, 0, "nothing competes with it for the room");
});
