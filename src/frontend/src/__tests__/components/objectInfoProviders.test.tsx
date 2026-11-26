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
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
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
            "selection-summary", "clash-contact", "fem-concepts", "source-jump", "object-metadata", "fea-element", "fea-sets", "indexed-attributes",
            "related", "element-links", "tree-node", "cellbuilder-item", "cellbuilder-structure",
        ],
    );
    const ids = (sel: Snapshot) => providersFor(sel, CORE_PROPERTY_PROVIDERS, []).map((p) => p.id);
    // clash-contact / fem-concepts / tree-node each read their own pick store, never cleared on a
    // mode switch and never cleared by a concept click either: picking a
    // FEM concept clears the scene selection (kind becomes "none"), so they are gated on not being a
    // builder pick (cell/ref) and not a "builder"-only panel, rather than on kind === "mesh" -- see
    // the dedicated test below for the "none" case.
    assert.deepEqual(ids(none), ["clash-contact", "fem-concepts", "source-jump", "indexed-attributes", "related", "tree-node"]);
    assert.deepEqual(ids({ ...none, kind: "mesh", name: "e", meshName: "e", count: 1 }), [
        "selection-summary", "clash-contact", "fem-concepts", "source-jump", "object-metadata", "indexed-attributes", "related", "tree-node",
    ]);
    // The Results section: only for a selection of nodes of a stored model.
    const nodes = [{ key: "file:m.ifc#p:RACK" }];
    assert.ok(ids({ ...none, kind: "mesh", name: "e", meshName: "e", count: 1, nodes }).includes("element-links"));
    assert.ok(!ids({ ...none, kind: "mesh", name: "e", meshName: "e", count: 1 }).includes("element-links"));
    assert.ok(ids({ ...none, hasEntities: true }).includes("selection-summary"), "the summary stays while the scene holds anything");
    const cell = ids({ ...none, kind: "cell", name: "c", meshName: "m", count: 1, cellBuilderActive: true });
    assert.deepEqual(cell, ["selection-summary", "object-metadata", "cellbuilder-item"], "no mesh-only section leaks into a cell pick");
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
            "selection-summary", "clash-contact", "fem-concepts", "source-jump", "object-metadata", "tie", "between",
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

test("summary 'menu': no name row, hide buttons, notices, Clicked at or Mesh section -- one ⋯ menu; the sections below still get the pick", () => {
    setServerState(useObjectInfoStore, { name: "E163", clickCoordinate: { x: 1.264, y: 0.5, z: 0.402 } } as never);
    const off = registerPropertyProvider({ id: "probe", order: 36, match: (sel) => sel.kind === "mesh", render: (sel) => React.createElement("p", { id: "probe" }, `item ${sel.meshName}`) });
    try {
        const full = render({ flat: true });
        assert.match(full, /aria-label="Unhide all"/, "full: as before");
        assert.match(full, /Clicked at:/);
        const menu = render({ flat: true, summary: "menu" });
        assert.match(menu, /data-summary-menu=""/);
        assert.match(menu, /aria-label="Selection actions"[^>]*>⋯</);
        for (const gone of [/Name:/, /aria-label="Unhide all"/, /aria-label="Hide selected"/, /Hide others/, /Clicked at:/, /Mesh \(/, /Copy name to clipboard/]) {
            assert.doesNotMatch(menu, gone, String(gone));
        }
        assert.match(menu, /<p id="probe">item E163<\/p>/, "an item below still gets the pick");
        assert.doesNotMatch(menu, /data-summary-menu-items/, "the menu is closed until asked");
        // Nothing picked: no menu either.
        setServerState(useObjectInfoStore, { name: "", clickCoordinate: null } as never);
        assert.doesNotMatch(render({ summary: "menu" }), /data-summary-menu/);
    } finally {
        off();
        setServerState(useObjectInfoStore, { name: "", clickCoordinate: null } as never);
    }
});

test("the summary menu's mesh line", async () => {
    const { meshStatsLine } = await import("@/components/info_box_selected_object/MeshStatsSection");
    assert.equal(meshStatsLine({ triangles: 2, vertices: 4, area: 1 }), "2 tris · 4 verts · 1.00 m²");
});

// ── selectionSource: each mode keeps its own kind of pick ──────────────────────────

const cbPristine = { ...useCellBuilderStore.getInitialState() };
/** Render with the builder store's current (live) state as the SSR snapshot too -- `render()`
 *  above reads the stores through `useSyncExternalStore`, whose server snapshot is
 *  `getInitialState()`, not `getState()` (same pattern as builderSections.test.tsx's `inViewer`). */
function renderWithBuilder(props?: Props): string {
    Object.assign(useCellBuilderStore.getInitialState(), useCellBuilderStore.getState());
    try {
        return render(props);
    } finally {
        Object.assign(useCellBuilderStore.getInitialState(), cbPristine);
    }
}

const builderDoc = { schema_version: "1.1", grid: {}, blueprint: {}, spaces: [{ NAME: "S1", X: 0, Y: 0, Z: 0, DX: 1, DY: 1, DZ: 1 }], equipments: [] } as never;

test("selectionSource='builder' never shows a scene pick, however stale, falling through to 'none'", () => {
    const cb = useCellBuilderStore.getState();
    // A builder item picked AND a leftover scene pick (as if Output was visited earlier and left
    // its mesh pick in the shared stores) at once.
    cb.open("m", "Model", 1, builderDoc);
    const sId = Object.values(useCellBuilderStore.getState().cells)[0].id;
    cb.setSelection({ kind: "cell", cellId: sId });
    setServerState(useObjectInfoStore, { name: "StaleMeshPick" });
    const seen: string[] = [];
    const off = registerPropertyProvider({ id: "probe", order: 1000, match: () => true, render: (sel) => (seen.push(`${sel.kind}:${sel.name}`), null) });
    try {
        seen.length = 0;
        renderWithBuilder({ selectionSource: "builder" });
        assert.ok(seen.includes("cell:S1"), `builder's own pick still shows: ${seen}`);
        assert.ok(!seen.some((s) => s.includes("StaleMeshPick")), `the stale scene pick never leaks in: ${seen}`);

        seen.length = 0;
        renderWithBuilder({ selectionSource: "scene" });
        assert.ok(seen.includes("mesh:StaleMeshPick"), `'scene' shows the scene pick instead: ${seen}`);
        assert.ok(!seen.some((s) => s.startsWith("cell:")), `and never the builder pick: ${seen}`);

        seen.length = 0;
        renderWithBuilder(); // "any" (default): today's rule, the builder pick wins when there is one
        assert.ok(seen.includes("cell:S1"));
    } finally {
        off();
        setServerState(useObjectInfoStore, { name: "" });
        cb.close();
    }
});

test("the stale-pick sections (clash-contact, fem-concepts, tree-node) stay silent under a builder pick, selectionSource='builder'", async () => {
    const { useClashContactPickStore } = await import("@/state/clashContactPickStore");
    const cb = useCellBuilderStore.getState();
    cb.open("m", "Model", 1, builderDoc);
    const sId = Object.values(useCellBuilderStore.getState().cells)[0].id;
    cb.setSelection({ kind: "cell", cellId: sId });
    setServerState(useTreeViewStore, { treeData: { id: "root", name: "root", children: [] } });
    // Synced to BOTH live and initial state (setServerState): the SSR render below reads the
    // server snapshot, so a staleness check needs the stale pick to actually be there for it,
    // not only in the live state nothing here reads.
    setServerState(useClashContactPickStore, { jointId: "stale-joint" }); // no matching result -> would render null anyway, were it even reached
    try {
        const html = renderWithBuilder({ selectionSource: "builder" });
        assert.doesNotMatch(html, /Spatial container|Hierarchy/, "tree-node stays out");
        assert.match(html, /data-builder-section="item"/, "the builder item still shows");
    } finally {
        setServerState(useTreeViewStore, { treeData: null });
        setServerState(useClashContactPickStore, { jointId: null });
        cb.close();
    }
});

// A click on a FEM concept's glyph clears the viewport selection (selectClickedConcept,
// conceptPicking.ts), so the snapshot's `kind` is "none" with nothing else picked -- it used to be
// gated on kind === "mesh", so the fem-concepts section never showed at all.
test("a FEM concept picked with nothing else picked shows with source 'any'/'scene', and is hidden with 'builder'", async () => {
    const { useFemConceptsStore } = await import("@/state/femConceptsStore");
    setServerState(useFemConceptsStore, {
        masses: [{ name: "M1", position: [1, 2, 3], mass: 500 }],
        selection: [{ kind: "mass", index: 0 }],
    } as never);
    try {
        assert.match(renderWithBuilder({ selectionSource: "any" }), />M1</, "the default shows it");
        assert.match(renderWithBuilder({ selectionSource: "scene" }), />M1</, "and so does 'scene'");
        assert.doesNotMatch(renderWithBuilder({ selectionSource: "builder" }), />M1</, "but not a builder-only panel");
    } finally {
        setServerState(useFemConceptsStore, { masses: [], selection: [] } as never);
    }
});
