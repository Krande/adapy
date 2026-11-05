// The Scene tree's options: absent, the tree renders exactly as it did; present, each does its one
// thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { default: TreeViewComponent } = await import("@/components/tree_view/TreeViewComponent");
const { CustomNode } = await import("@/components/tree_view/CustomNode");
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { registerSceneTreeRoots, claimSceneTreeSelection, sceneTreeContributedRoots } = await import(
    "@/plugins/sceneTreeRoots"
);

type Props = import("@/components/tree_view/TreeViewComponent").SceneTreeProps;
type Row = import("@/components/tree_view/CustomNode").TreeNodeData;

const row = (id: string, name: string, extra: Partial<Row> = {}): Row => ({ id, name, children: [], ...extra });

function render(props?: Props): string {
    return renderToStaticMarkup(
        React.createElement(AdaViewerProvider, null, React.createElement(TreeViewComponent, props)),
    );
}

setServerState(useTreeViewStore, {
    treeData: row("root", "root", { children: [row("a", "Alpha"), row("b", "Bravo")] }),
});

/** One row as the tree draws it, outside a tree: enough of `NodeApi` for `CustomNode`. */
function renderRow(data: Row): string {
    const node = {
        data,
        isSelected: false,
        isOpen: false,
        children: [],
        level: 1,
        tree: { props: { data: [] } },
        toggle() {},
    };
    return renderToStaticMarkup(
        React.createElement(CustomNode, { node, style: {}, dragHandle: undefined } as never),
    );
}

test("without options the tree renders as with an empty options object", () => {
    const plain = render();
    assert.equal(render({}), plain);
    assert.equal(render({ rootFilter: undefined, extraRoots: [], header: undefined }), plain);
    assert.doesNotMatch(plain, /more loaded/);
});

test("a header renders under the search row", () => {
    const html = render({ header: React.createElement("p", { id: "probe" }, "summary line") });
    assert.match(html, /<p id="probe">summary line<\/p>/);
    assert.ok(html.indexOf("probe") > html.indexOf("Search the scene"));
});

test("a root filter that hides roots offers them back, and one that hides none says nothing", () => {
    const hiding = render({ rootFilter: (roots) => ({ shown: roots.slice(0, 1), hidden: roots.length - 1 }) });
    assert.match(hiding, /1 more loaded \u2014 show all/);
    const none = render({ rootFilter: (roots) => ({ shown: roots, hidden: 0 }) });
    assert.equal(none, render());
});

test("a row's meta renders at its right edge, and a row without one is unchanged", () => {
    const plain = renderRow(row("a", "Alpha"));
    assert.equal(renderRow(row("a", "Alpha", { meta: undefined })), plain);
    assert.doesNotMatch(plain, /tabular-nums/);
    const withMeta = renderRow(row("a", "Alpha", { meta: "12" }));
    assert.match(withMeta, /<span class="shrink-0 pl-2 text-\[10px\] tabular-nums text-gray-400">12<\/span>/);
    assert.ok(withMeta.indexOf(">12<") > withMeta.indexOf(">Alpha<"));
});

test("the filter is called with the model roots and showAll false at first", () => {
    const seen: [string[], boolean][] = [];
    render({ rootFilter: (roots, showAll) => (seen.push([roots.map((r) => r.id), showAll]), { shown: roots, hidden: 0 }) });
    assert.deepEqual(seen[0], [["a", "b"], false]);
});

test("contributed roots come after the model roots, and a provider may take a selection", () => {
    let notify = () => {};
    const rows = [row("extra:1", "Extra")];
    const taken: string[][] = [];
    const off = registerSceneTreeRoots({
        id: "probe",
        roots: () => rows,
        subscribe: (cb) => ((notify = cb), () => {}),
        onSelect: (ids) => {
            if (!ids.some((id) => id.startsWith("extra:"))) return false;
            taken.push([...ids]);
            return true;
        },
    });
    assert.deepEqual(sceneTreeContributedRoots(), rows);
    assert.equal(sceneTreeContributedRoots(), sceneTreeContributedRoots(), "the snapshot must be stable");
    const before = sceneTreeContributedRoots();
    notify();
    assert.notEqual(sceneTreeContributedRoots(), before, "a provider change must refresh the snapshot");
    assert.equal(claimSceneTreeSelection(["a"]), false);
    assert.equal(claimSceneTreeSelection(["extra:1"]), true);
    assert.deepEqual(taken, [["extra:1"]]);
    off();
    assert.deepEqual(sceneTreeContributedRoots(), []);
    assert.equal(claimSceneTreeSelection(["extra:1"]), false);
});
