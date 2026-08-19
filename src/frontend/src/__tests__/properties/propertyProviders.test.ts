import assert from "node:assert/strict";
import {test, beforeEach} from "node:test";

import {
    _resetPropertyProviders,
    allProviders,
    providersFor,
    registerPropertyProvider,
    unregisterPropertyProvider,
    type SelectionSnapshot,
} from "../../components/properties/propertyProviders";
import {CORE_PROVIDER_RULES} from "../../components/properties/coreProviderRules";

// The Properties registry is what replaces "N bespoke info boxes each deciding for
// itself whether to appear". These tests pin the two properties that make it worth
// having: deterministic composition order, and failure isolation — a plugin's provider
// must not be able to blank the panel for everything else.

const sel = (over: Partial<SelectionSnapshot> = {}): SelectionSnapshot => ({
    kind: "none",
    name: null,
    count: 0,
    hasEntities: false,
    cellBuilderActive: false,
    ...over,
});

beforeEach(_resetPropertyProviders);

const stub = (id: string, order: number, match: (s: SelectionSnapshot) => boolean) =>
    registerPropertyProvider({id, order, match, render: () => null});

test("providers render in ascending order regardless of registration order", () => {
    stub("third", 20, () => true);
    stub("first", 0, () => true);
    stub("second", 10, () => true);

    assert.deepEqual(providersFor(sel()).map((p) => p.id), ["first", "second", "third"]);
});

test("a provider with no order sorts as 0", () => {
    registerPropertyProvider({id: "unordered", match: () => true, render: () => null});
    stub("later", 5, () => true);
    assert.deepEqual(providersFor(sel()).map((p) => p.id), ["unordered", "later"]);
});

test("only matching providers are returned", () => {
    stub("mesh-only", 0, (s) => s.kind === "mesh");
    stub("cell-only", 1, (s) => s.kind === "cell");

    assert.deepEqual(providersFor(sel({kind: "mesh"})).map((p) => p.id), ["mesh-only"]);
    assert.deepEqual(providersFor(sel({kind: "cell"})).map((p) => p.id), ["cell-only"]);
    assert.deepEqual(providersFor(sel()).map((p) => p.id), []);
});

test("a throwing match disables only that provider", () => {
    // Failure isolation. A plugin whose predicate throws must not take the panel down —
    // the same discipline the plugin registry applies to slot rendering.
    stub("good-before", 0, () => true);
    registerPropertyProvider({
        id: "explodes",
        order: 1,
        match: () => {
            throw new Error("boom");
        },
        render: () => null,
    });
    stub("good-after", 2, () => true);

    assert.deepEqual(providersFor(sel()).map((p) => p.id), ["good-before", "good-after"]);
});

test("re-registering an id replaces rather than duplicates", () => {
    stub("dup", 0, () => true);
    stub("dup", 5, () => true);
    const all = allProviders();
    assert.equal(all.length, 1);
    assert.equal(all[0].order, 5);
});

test("a provider without an id is rejected, not registered", () => {
    registerPropertyProvider({id: "", match: () => true, render: () => null});
    assert.equal(allProviders().length, 0);
});

test("unregister removes a provider", () => {
    stub("temp", 0, () => true);
    assert.equal(allProviders().length, 1);
    unregisterPropertyProvider("temp");
    assert.equal(allProviders().length, 0);
});

test("core's providers cover the selection kinds they claim", () => {
    // Uses the REAL rules, not stubs. (The render half imports the whole viewer —
    // cellBuilderStore reaches a vite `?worker&inline` module only a bundler can
    // resolve — which is exactly why the predicates were split into their own module.)
    _resetPropertyProviders();
    for (const r of CORE_PROVIDER_RULES) {
        registerPropertyProvider({...r, render: () => null});
    }

    const ids = (s: SelectionSnapshot) => providersFor(s).map((p) => p.id);

    assert.deepEqual(ids(sel({kind: "mesh", name: "BM1", count: 1})), [
        "selection-summary",
        "object-metadata",
    ]);
    assert.deepEqual(ids(sel({kind: "cell", name: "CELL1", count: 1, cellBuilderActive: true})), [
        "selection-summary",
        "cellbuilder-cell",
    ]);
    // Nothing selected, nothing loaded: the panel shows its empty state instead.
    assert.deepEqual(ids(sel()), []);
    // Nothing selected but a model IS loaded: scene-wide actions stay reachable.
    assert.deepEqual(ids(sel({hasEntities: true})), ["selection-summary"]);
});

test("core provider order leaves room for plugins to slot between", () => {
    for (const r of CORE_PROVIDER_RULES) registerPropertyProvider({...r, render: () => null});
    // Gaps of ten. A plugin inserting at 15 must land between metadata and the cell
    // detail without core renumbering.
    const orders = allProviders().map((p) => p.order ?? 0);
    for (let i = 1; i < orders.length; i++) {
        assert.ok(orders[i] - orders[i - 1] >= 5, "core providers should leave gaps");
    }
});
