// The Scene panel's options: absent, the panel renders as it did; present, each does its one thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { default: SceneBody } = await import("@/components/info_box_scene/SceneBody");
const { useSceneInfoStore } = await import("@/state/sceneInfoStore");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const {
    activeSceneTab,
    groupOf,
    sceneGroups,
    sceneTabs,
    shouldStackTabs,
    STACK_ENTER_BODY_PX,
    STACK_HEADER_PX,
    STACK_LEAVE_BODY_PX,
    SUB_LABEL,
} = await import("@/components/info_box_scene/sceneTabs");

type Props = import("@/components/info_box_scene/SceneBody").SceneBodyProps;

function render(props?: Props): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(SceneBody, props)));
}

const tabLabels = (html: string) => [...html.matchAll(/role="tab"[^>]*>(?:<span[^>]*><\/span>)?([^<]+)</g)].map((m) => m[1]);

test("without options the panel renders as with an empty options object", () => {
    const plain = render();
    assert.equal(render({}), plain);
    assert.equal(render({ arrangement: "tabs", femTab: false, loadedTitle: "Loaded models" }), plain);
    // The strip shows groups; Model is one tab, so no second strip.
    assert.deepEqual(tabLabels(plain), ["Model", "View", "Joints", "Tools"]);
    assert.match(plain, /<span>Loaded models<\/span>/);
    assert.match(plain, /No models loaded\./);
});

test("tabs picks and orders the tabs; hiddenTabs leaves some out", () => {
    assert.deepEqual(tabLabels(render({ tabs: ["tools", "model"] })), ["Tools", "Model"]);
    // Joints loses its tabs (Clashes, Overlays; Review needs joints), so the group goes too.
    assert.deepEqual(tabLabels(render({ hiddenTabs: ["clip", "clashes", "jointOverlays"] })), ["Model", "View", "Tools"]);
});

test("a group of several tabs shows them in a second strip, on the one the mode names", () => {
    setServerState(useSceneInfoStore, { mode: "section" });
    try {
        const html = render();
        assert.deepEqual(tabLabels(html), ["Model", "View", "Joints", "Tools", "Overlays", "Clip", "Mesh"]);
        assert.match(html, /role="tablist" aria-label="View section"/);
        assert.match(html, /aria-selected="true"[^>]*>View</);
        assert.match(html, /aria-selected="true"[^>]*>Clip</);
        // Hidden tabs leave the group's strip, and a group down to one tab draws none.
        assert.deepEqual(tabLabels(render({ hiddenTabs: ["overlays", "mesh"] })), ["Model", "View", "Joints", "Tools"]);
    } finally {
        setServerState(useSceneInfoStore, { mode: "info" });
    }
});

test("the FEM tab is offered only when asked for, and only while there is FE content", () => {
    assert.deepEqual(tabLabels(render({ femTab: true })), tabLabels(render()));
    setServerState(useFeaAnimationStore, { sessionActive: true });
    try {
        assert.deepEqual(tabLabels(render({ femTab: true })), ["Model", "View", "FEM", "Joints", "Tools"]);
        assert.doesNotMatch(render(), /FEM/);
        setServerState(useSceneInfoStore, { mode: "fem" });
        assert.match(render({ femTab: true }), /aria-selected="true"[^>]*><span[^>]*><\/span>FEM</);
        assert.match(render(), /aria-selected="true"[^>]*>Model</, "without femTab the fem mode still lands on Model");
    } finally {
        setServerState(useFeaAnimationStore, { sessionActive: false });
        setServerState(useSceneInfoStore, { mode: "info" });
    }
});

test("stacked drops the strip and draws each tab as a section, the active one open", () => {
    const html = render({ arrangement: "stacked" });
    assert.doesNotMatch(html, /role="tablist"/);
    for (const label of ["Model", "Overlays", "Tools", "Clip", "Mesh", "Clashes"]) {
        assert.match(html, new RegExp(`<span>${label}</span>`));
    }
    assert.match(html, /aria-expanded="true"[^>]*>.*?<span>Model<\/span>/);
});

test("auto renders the strip until it has measured the body", () => {
    assert.equal(render({ arrangement: "auto" }), render());
});

test("the loaded list takes a title and an empty state", () => {
    const html = render({ loadedTitle: "Open files", loadedEmpty: React.createElement("p", { id: "empty" }, "None yet") });
    assert.match(html, /<span>Open files<\/span>/);
    assert.match(html, /<p id="empty">None yet<\/p>/);
    assert.doesNotMatch(html, /No models loaded\./);
});

test("sceneTabs and activeSceneTab", () => {
    const ids = (t: { id: string }[]) => t.map((x) => x.id);
    assert.deepEqual(ids(sceneTabs({}, {})), ["model", "overlays", "clip", "mesh", "clashes", "jointOverlays", "tools"]);
    assert.deepEqual(ids(sceneTabs({}, { joints: true })), ["model", "overlays", "clip", "mesh", "clashes", "joints", "jointOverlays", "tools"]);
    assert.deepEqual(ids(sceneTabs({ femTab: true }, { fem: true })), ["model", "overlays", "clip", "mesh", "fem", "clashes", "jointOverlays", "tools"]);
    assert.deepEqual(ids(sceneTabs({ tabs: ["joints", "tools"] }, {})), ["tools"], "a contextual tab still needs content");
    const offered = sceneTabs({ tabs: ["tools", "mesh"] }, {});
    assert.equal(activeSceneTab("info", offered), "tools", "with no Model tab the first offered one is active");
    assert.equal(activeSceneTab("mesh", offered), "mesh");
    assert.equal(activeSceneTab("fem", sceneTabs({}, {})), "model");
});

test("sceneGroups groups the offered tabs, in the order their first tab is offered", () => {
    const shape = (tabs: Parameters<typeof sceneGroups>[0]) => sceneGroups(tabs).map((g) => [g.id, g.tabs.map((t) => t.id)]);
    assert.deepEqual(shape(sceneTabs({}, { connection: true, joints: true })), [
        ["model", ["model"]],
        ["view", ["overlays", "clip", "mesh"]],
        ["joints", ["connection", "clashes", "joints", "jointOverlays"]],
        ["tools", ["tools"]],
    ]);
    assert.deepEqual(shape(sceneTabs({ tabs: ["clashes", "tools", "clip"] }, {})), [
        ["joints", ["clashes"]],
        ["tools", ["tools"]],
        ["view", ["clip"]],
    ]);
    assert.equal(groupOf("joints"), "joints");
    assert.equal(groupOf("mesh"), "view");
    assert.equal(SUB_LABEL.joints, "Review");
    assert.equal(SUB_LABEL.jointOverlays, "Overlays");
    assert.equal(groupOf("jointOverlays"), "joints");
});

test("shouldStackTabs enters above its threshold and leaves only below a lower one", () => {
    const n = 5;
    const enter = n * STACK_HEADER_PX + STACK_ENTER_BODY_PX;
    const leave = n * STACK_HEADER_PX + STACK_LEAVE_BODY_PX;
    assert.equal(shouldStackTabs(n, enter - 1, false), false);
    assert.equal(shouldStackTabs(n, enter, false), true);
    assert.equal(shouldStackTabs(n, leave, true), true);
    assert.equal(shouldStackTabs(n, leave - 1, true), false);
    assert.equal(shouldStackTabs(2, 10_000, false), false);
});
