// The cellbuilder panel's options: absent, the panel renders as it did; present, each does its one thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { CellBuilderBody } = await import("@/components/viewer/CellBuilderPanel");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { offeredTabs, shownTab } = await import("@/components/viewer/cellbuilder/panel/chrome");

type Props = import("@/components/viewer/CellBuilderPanel").CellBuilderBodyProps;

function render(props?: Props): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(CellBuilderBody, props)));
}

const tabLabels = (html: string) => [...html.matchAll(/role="tab"[^>]*>([^<]+)/g)].map((m) => m[1]);

setServerState(useCellBuilderStore, { active: { modelId: "m-1", name: "Model A", revision: 2 } });

test("without options the panel renders as with an empty options object, and as with the defaults", () => {
    const plain = render();
    assert.equal(render({}), plain);
    assert.equal(
        render({
            tabs: ["build", "equipment", "systems", "detailing", "view", "tools"],
            showUndoRedo: true,
            showAddButtons: true,
            showComponentList: true,
        }),
        plain,
    );
    assert.deepEqual(tabLabels(plain), ["Build", "Equipment", "Systems", "View", "Tools"]);
    assert.match(plain, /title="Undo \(Ctrl\+Z\)"/);
    assert.match(plain, />\+ Cell</);
    assert.match(plain, /Cells &amp; equipment/);
});

test("tabs picks and orders the tabs, and tabLabels renames them", () => {
    assert.deepEqual(tabLabels(render({ tabs: ["tools", "build"] })), ["Tools", "Build"]);
    assert.deepEqual(tabLabels(render({ tabs: ["build", "tools"], tabLabels: { tools: "More" } })), ["Build", "More"]);
    const html = render({ tabs: ["tools", "view"] });
    assert.match(html, /aria-selected="true"[^>]*>Tools/, "without Build the first offered tab is shown");
});

test("the header and Build tab parts can be left out", () => {
    const html = render({ showUndoRedo: false, showAddButtons: false, showComponentList: false });
    assert.doesNotMatch(html, /Undo \(Ctrl\+Z\)/);
    assert.doesNotMatch(html, /Redo/);
    assert.match(html, /title="Close model"/);
    assert.doesNotMatch(html, />\+ Cell</);
    assert.doesNotMatch(html, /Cells &amp; equipment/);
    assert.match(html, /Grid &amp; snapping/);
});

test("emptyState replaces the panel while no model is open", () => {
    const empty = React.createElement("p", { id: "empty" }, "Nothing open");
    assert.doesNotMatch(render({ emptyState: empty }), /id="empty"/, "a model is open");
    setServerState(useCellBuilderStore, { active: null });
    try {
        assert.equal(render({ emptyState: empty }), '<p id="empty">Nothing open</p>');
        assert.match(render(), /data-builder-no-model/, "without it the panel offers a way to start a model");
    } finally {
        setServerState(useCellBuilderStore, { active: { modelId: "m-1", name: "Model A", revision: 2 } });
    }
});

test("offeredTabs and shownTab", () => {
    assert.deepEqual(offeredTabs(undefined, false), ["build", "equipment", "systems", "view", "tools"]);
    assert.deepEqual(offeredTabs(undefined, true), ["build", "equipment", "systems", "detailing", "view", "tools"]);
    assert.deepEqual(offeredTabs(["detailing", "tools", "tools"], false), ["tools"]);
    assert.equal(shownTab("systems", ["tools", "systems"]), "systems");
    assert.equal(shownTab("build", ["tools"]), "tools");
    assert.equal(shownTab("build", []), "build");
});
