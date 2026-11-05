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
const { activeSceneTab, sceneTabs, shouldStackTabs, STACK_ENTER_BODY_PX, STACK_HEADER_PX, STACK_LEAVE_BODY_PX } =
    await import("@/components/info_box_scene/sceneTabs");

type Props = import("@/components/info_box_scene/SceneBody").SceneBodyProps;

function render(props?: Props): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(SceneBody, props)));
}

const tabLabels = (html: string) => [...html.matchAll(/role="tab"[^>]*>(?:<span[^>]*><\/span>)?([^<]+)</g)].map((m) => m[1]);

test("without options the panel renders as with an empty options object", () => {
    const plain = render();
    assert.equal(render({}), plain);
    assert.equal(render({ arrangement: "tabs", femTab: false, loadedTitle: "Loaded models" }), plain);
    assert.deepEqual(tabLabels(plain), ["Model", "Overlays", "Tools", "Clip", "Mesh", "Clashes"]);
    assert.match(plain, /<span>Loaded models<\/span>/);
    assert.match(plain, /No models loaded\./);
});

test("tabs picks and orders the tabs; hiddenTabs leaves some out", () => {
    assert.deepEqual(tabLabels(render({ tabs: ["tools", "model"] })), ["Tools", "Model"]);
    assert.deepEqual(tabLabels(render({ hiddenTabs: ["clip", "clashes"] })), ["Model", "Overlays", "Tools", "Mesh"]);
});

test("the FEM tab is offered only when asked for, and only while there is FE content", () => {
    assert.deepEqual(tabLabels(render({ femTab: true })), tabLabels(render()));
    setServerState(useFeaAnimationStore, { sessionActive: true });
    try {
        assert.deepEqual(tabLabels(render({ femTab: true })), ["Model", "Overlays", "Tools", "Clip", "Mesh", "FEM", "Clashes"]);
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
    assert.deepEqual(ids(sceneTabs({}, {})), ["model", "overlays", "tools", "clip", "mesh", "clashes"]);
    assert.deepEqual(ids(sceneTabs({}, { joints: true })), ["model", "overlays", "tools", "clip", "mesh", "clashes", "joints"]);
    assert.deepEqual(ids(sceneTabs({ femTab: true }, { fem: true })), ["model", "overlays", "tools", "clip", "mesh", "fem", "clashes"]);
    assert.deepEqual(ids(sceneTabs({ tabs: ["joints", "tools"] }, {})), ["tools"], "a contextual tab still needs content");
    const offered = sceneTabs({ tabs: ["tools", "mesh"] }, {});
    assert.equal(activeSceneTab("info", offered), "tools", "with no Model tab the first offered one is active");
    assert.equal(activeSceneTab("mesh", offered), "mesh");
    assert.equal(activeSceneTab("fem", sceneTabs({}, {})), "model");
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
