// The Selected Object panel as an ordered list of providers: core's sections draw what they always
// did, a registered provider slots in by order, and `onShowInData` leaves the markup alone.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ObjectInfoBody, CORE_PROPERTY_PROVIDERS } = await import(
    "@/components/info_box_selected_object/ObjectInfoBoxComponent"
);
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { useObjectInfoStore } = await import("@/state/objectInfoStore");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { providersFor, registerPropertyProvider, listPropertyProviders } = await import("@/plugins/propertyProviders");

type Props = import("@/components/info_box_selected_object/ObjectInfoBoxComponent").ObjectInfoBodyProps;
type Snapshot = import("@/plugins/propertyProviders").SelectionSnapshot;

function render(props?: Props): string {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(ObjectInfoBody, props)));
}

const none: Snapshot = { kind: "none", name: null, meshName: null, count: 0, hasEntities: false, cellBuilderActive: false };

test("core's sections keep their order and rules", () => {
    assert.deepEqual(
        CORE_PROPERTY_PROVIDERS.map((p) => p.id),
        [
            "selection-summary", "fem-concepts", "source-jump", "object-metadata", "fea-sets", "indexed-attributes", "related", "tree-node",
            "cellbuilder-item", "cellbuilder-structure",
        ],
    );
    const ids = (sel: Snapshot) => providersFor(sel, CORE_PROPERTY_PROVIDERS, []).map((p) => p.id);
    assert.deepEqual(ids(none), ["fem-concepts", "source-jump", "indexed-attributes", "related", "tree-node"]);
    assert.ok(ids({ ...none, hasEntities: true }).includes("selection-summary"), "the summary stays while the scene holds anything");
    const cell = ids({ ...none, kind: "cell", name: "c", meshName: "m", count: 1, cellBuilderActive: true });
    assert.deepEqual(cell, ["selection-summary", "fem-concepts", "object-metadata", "tree-node", "cellbuilder-item"]);
});

test("a plugin provider slots in by order, after core's on a tie, and a throwing match is skipped", () => {
    const sel = { ...none, kind: "mesh" as const, name: "e", meshName: "e", count: 1 };
    const plugins = [
        { id: "late", order: 100, match: () => true, render: () => null },
        { id: "between", order: 35, match: () => true, render: () => null },
        { id: "tie", order: 30, match: () => true, render: () => null },
        { id: "broken", order: 1, match: () => { throw new Error("x"); }, render: () => null },
    ];
    const warn = console.warn;
    console.warn = () => {};
    try {
        const ids = providersFor(sel, CORE_PROPERTY_PROVIDERS, plugins).map((p) => p.id);
        assert.deepEqual(ids, [
            "selection-summary", "fem-concepts", "source-jump", "object-metadata", "tie", "between",
            "indexed-attributes", "related", "tree-node", "late",
        ]);
    } finally {
        console.warn = warn;
    }
});

test("an empty scene renders nothing, and Unhide all appears once the scene holds a model", () => {
    assert.equal(render(), "");
    setServerState(useTreeViewStore, { treeData: { id: "root", name: "root", children: [] } });
    try {
        assert.match(render(), /aria-label="Unhide all"/);
        assert.doesNotMatch(render(), /Hide selected/);
    } finally {
        setServerState(useTreeViewStore, { treeData: null });
    }
});

test("a registered provider renders in the panel, and is gone once removed", () => {
    setServerState(useObjectInfoStore, { name: "Element 1" });
    try {
        const plain = render();
        const off = registerPropertyProvider({
            id: "probe",
            order: 35,
            match: (sel) => sel.kind === "mesh",
            render: (sel) => React.createElement("p", { id: "probe" }, `about ${sel.name}`),
        });
        assert.equal(listPropertyProviders().length, 1);
        const html = render();
        assert.match(html, /<p id="probe">about Element 1<\/p>/);
        assert.ok(html.indexOf("probe") > html.indexOf("Element 1"), "after the summary");
        off();
        assert.equal(render(), plain);
    } finally {
        setServerState(useObjectInfoStore, { name: "" });
    }
});

test("onShowInData leaves the markup as it is, the Show in data button included", () => {
    const mesh = { drawRanges: new Map([["E7", [0, 3]]]), geometry: { getIndex: () => ({ array: new Uint32Array([4, 5, 6]) }) } };
    setServerState(useFeaAnimationStore, { sessionActive: true, mesh } as never);
    setServerState(useObjectInfoStore, { name: "E7" });
    try {
        const plain = render();
        assert.match(plain, /Show in data/);
        assert.match(plain, /scroll to node 5 /);
        assert.equal(render({ onShowInData: () => {} }), plain);
    } finally {
        setServerState(useFeaAnimationStore, { sessionActive: false, mesh: null } as never);
        setServerState(useObjectInfoStore, { name: "" });
    }
});

test("a shell's `sections` keeps a section out by id, given the shell's mode on the snapshot; a throwing filter keeps it", async () => {
    const { visibleSections } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");
    const { setShellModes } = await import("@/layout/shellModes");
    setServerState(useObjectInfoStore, { name: "Element 1" });
    const off = registerPropertyProvider({
        id: "probe",
        order: 35,
        match: () => true,
        render: (sel) => React.createElement("p", { id: "probe" }, `in ${sel.shellMode ?? "no mode"}`),
    });
    try {
        assert.match(render(), /<p id="probe">in no mode<\/p>/, "no shell modes: no shellMode");
        setShellModes([{ id: "output", label: "Output", order: 0 }], "output");
        assert.match(render(), /<p id="probe">in output<\/p>/);
        const seen: string[] = [];
        const html = render({ sections: (id, sel) => (seen.push(`${id}@${sel.shellMode}`), id !== "probe") });
        assert.doesNotMatch(html, /id="probe"/);
        assert.match(html, /Element 1/, "the others stay");
        assert.ok(seen.includes("probe@output"));

        const list = providersFor({ ...none, kind: "mesh", name: "x", meshName: "x" }, CORE_PROPERTY_PROVIDERS, []);
        const boom = () => {
            throw new Error("bug");
        };
        assert.deepEqual(visibleSections(list, none, boom).map((p) => p.id), list.map((p) => p.id));
        assert.deepEqual(visibleSections(list, none, undefined).map((p) => p.id), list.map((p) => p.id));
    } finally {
        off();
        setShellModes([]);
        setServerState(useObjectInfoStore, { name: "" });
    }
});

test("`flat` draws the picked element's properties open, with no foldable heading, and the decimals menu on the clicked-at row", () => {
    setServerState(useObjectInfoStore, { name: "Element 1", clickCoordinate: { x: 1, y: 2, z: 3 } } as never);
    try {
        const folded = render();
        assert.match(folded, /aria-controls="object-properties"/, "the classic panel folds under a heading");
        assert.doesNotMatch(folded, /Clicked at:/, "and starts folded");
        const flat = render({ flat: true });
        assert.doesNotMatch(flat, /aria-controls="object-properties"/, "no chevron");
        assert.doesNotMatch(flat, /<span class="font-semibold">Properties<\/span>/, "no title");
        assert.match(flat, /data-object-metadata="flat"/);
        assert.match(flat, /Clicked at:[\s\S]*data-clicked-at=""[\s\S]*aria-label="Properties display options"/, "the menu sits in the row");
    } finally {
        setServerState(useObjectInfoStore, { name: "", clickCoordinate: null } as never);
    }
});
