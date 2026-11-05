// The Storage panel's options: absent, the panel renders as it did; present, each does its one thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { StorageBody } = await import("@/components/storage/StorageBrowser");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { unsavedModelName } = await import("@/components/storage/browser/UnsavedModelRow");

type Props = import("@/components/storage/StorageBrowser").StorageBodyProps;

function render(props?: Props): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(StorageBody, props)));
}

test("an open model missing from the saved list gets an unsaved row", () => {
    assert.doesNotMatch(render(), /unsaved/);
    setServerState(useCellBuilderStore, { active: { modelId: "m-1", name: "Draft model", revision: 0 } });
    try {
        const html = render();
        assert.match(html, /aria-label="Close Draft model"/);
        assert.match(html, />unsaved</);
    } finally {
        setServerState(useCellBuilderStore, { active: null });
    }
});

test("unsavedModelName", () => {
    const active = { modelId: "m-1", name: "A" };
    assert.equal(unsavedModelName(null, []), null);
    assert.equal(unsavedModelName(active, []), "A");
    assert.equal(unsavedModelName(active, [{ id: "m-2" }]), "A");
    assert.equal(unsavedModelName(active, [{ id: "m-1" }]), null);
});
