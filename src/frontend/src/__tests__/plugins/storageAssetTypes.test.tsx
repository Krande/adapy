/** The `storageAssetTypes` slot (plugin API 1.26.0): a plugin's kinds of stored asset in the
 * storage browser.
 *
 * Two halves. The registry's: which types a scope has, in which order, and what a type that
 * cannot answer costs its plugin. And the browser's: a section per type under the file list, and
 * the "+" menu's entries for the types that can be added to. The dialog an entry opens needs a
 * click, so what is held here is what it would be given: the entry, and its title.
 */
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { StorageBody } = await import("@/components/storage/StorageBrowser");
const { uploadDialogTitle, useStorageAssetTypes } = await import("@/components/storage/browser/AssetTypeSections");
const { useServerInfoStore } = await import("@/state/serverInfoStore");
const { makePluginContextStandalone } = await import("@/plugins/context");
const { getRegisteredPlugins, getStorageAssetTypes, registerPlugin, resetRegistry } = await import("@/plugins/registry");

type ServerFileEntry = import("@/state/serverInfoStore").ServerFileEntry;

const ctx = () => makePluginContextStandalone("");
const ids = (scope = "shared") => getStorageAssetTypes(ctx(), { scope }).map((e) => e.type.id);

function inViewer(node: React.ReactNode): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, node));
}

beforeEach(() => resetRegistry());

test("an asset type is namespaced to its plugin and listed by order, then id", () => {
    registerPlugin({
        id: "alpha",
        storageAssetTypes: [
            { id: "late", label: "Late", order: 5, render: () => null },
            { id: "b", label: "B", render: () => null },
        ],
    });
    registerPlugin({ id: "beta", storageAssetTypes: [{ id: "a", label: "A", render: () => null }] });
    assert.deepEqual(ids(), ["alpha:b", "beta:a", "alpha:late"]);
    assert.deepEqual(
        getStorageAssetTypes(ctx(), { scope: "shared" }).map((e) => e.pluginId),
        ["alpha", "beta", "alpha"],
    );
});

test("a type without an id, a label or a render is left out", () => {
    registerPlugin({
        id: "alpha",
        storageAssetTypes: [
            { id: "", label: "No id", render: () => null },
            { id: "nolabel", label: "", render: () => null },
            { id: "norender", label: "No render" } as never,
            { id: "ok", label: "Ok", render: () => null },
        ],
    });
    assert.deepEqual(ids(), ["alpha:ok"]);
});

test("available decides per scope; the plugin's activation predicate decides for all its types", () => {
    let active = true;
    registerPlugin({
        id: "alpha",
        activationPredicate: () => active,
        storageAssetTypes: [
            { id: "everywhere", label: "Everywhere", render: () => null },
            { id: "projects", label: "Projects", available: (_c, { scope }) => scope.startsWith("project:"), render: () => null },
        ],
    });
    assert.deepEqual(ids("shared"), ["alpha:everywhere"]);
    assert.deepEqual(ids("project:p1"), ["alpha:everywhere", "alpha:projects"]);
    active = false;
    assert.deepEqual(ids("project:p1"), []);
});

test("an available that throws disables its plugin, and the other plugins' types stay", () => {
    registerPlugin({
        id: "broken",
        storageAssetTypes: [
            { id: "x", label: "X", available: () => { throw new Error("boom"); }, render: () => null },
        ],
    });
    registerPlugin({ id: "fine", storageAssetTypes: [{ id: "y", label: "Y", render: () => null }] });
    assert.deepEqual(ids(), ["fine:y"]);
    assert.match(getRegisteredPlugins().find((p) => p.id === "broken")?.disabled ?? "", /available\(\) threw/);
});

test("the storage browser draws a section per type, filled by the plugin with the open scope", () => {
    const plain = inViewer(React.createElement(StorageBody));
    assert.doesNotMatch(plain, /storage-asset-types/, "no plugin, no sections");

    const seen: string[] = [];
    registerPlugin({
        id: "alpha",
        storageAssetTypes: [
            {
                id: "kits",
                label: "Kits",
                render: (c, { scope }) => {
                    seen.push(`${c.pluginId}|${scope}`);
                    return React.createElement("ul", { "data-testid": "kit-list" }, React.createElement("li", null, "kit one"));
                },
            },
            { id: "hidden", label: "Hidden kinds", available: () => false, render: () => "never" },
        ],
    });
    const html = inViewer(React.createElement(StorageBody));
    assert.match(html, /data-testid="storage-asset-types"/);
    assert.match(html, /data-asset-type="alpha:kits"/);
    assert.match(html, />Kits</);
    assert.match(html, /kit one/);
    assert.doesNotMatch(html, /Hidden kinds/);
    assert.equal(seen.length, 1);
    assert.match(seen[0], /^alpha\|/, "rendered with its own plugin's context");

    // Beside the files, not instead of them: a scope with files has both.
    const file = { name: "part.glb", fileType: 0, filepath: "part.glb", lastModified: "" } as ServerFileEntry;
    setServerState(useServerInfoStore, { serverFileObjects: [file] });
    try {
        const both = inViewer(React.createElement(StorageBody));
        assert.match(both, /part\.glb/);
        assert.match(both, /kit one/);
    } finally {
        setServerState(useServerInfoStore, { serverFileObjects: [] });
    }
});

test("the + menu gets an entry for each type that can be added to, and no other", () => {
    registerPlugin({
        id: "alpha",
        storageAssetTypes: [
            { id: "kits", label: "Kits", render: () => null, upload: { label: "Upload a kit…", render: () => "form" } },
            { id: "readonly", label: "Read-only", render: () => null },
            {
                id: "elsewhere",
                label: "Elsewhere",
                available: () => false,
                render: () => null,
                upload: { label: "Upload elsewhere…", render: () => "form" },
            },
        ],
    });
    let items: { key: string; label: string }[] = [];
    const Probe: React.FC = () => {
        const view = useStorageAssetTypes("shared");
        items = view.addItems.map((i) => ({ key: i.key, label: String(i.label) }));
        assert.equal(view.dialog, null, "no form is open until an entry is chosen");
        return null;
    };
    inViewer(React.createElement(Probe));
    assert.deepEqual(items, [{ key: "asset-type:alpha:kits", label: "Upload a kit…" }]);
});

test("the dialog is titled by the plugin, else by the entry without its ellipsis", () => {
    assert.equal(uploadDialogTitle({ label: "Upload a kit…" }), "Upload a kit");
    assert.equal(uploadDialogTitle({ label: "Upload a kit..." }), "Upload a kit");
    assert.equal(uploadDialogTitle({ label: "Upload a kit…", title: "New kit" }), "New kit");
});
