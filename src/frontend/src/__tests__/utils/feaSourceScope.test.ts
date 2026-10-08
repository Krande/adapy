import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {useScopeStore} from "@/state/scopeStore";
import {feaSourceScope} from "@/utils/scene/fea/streaming/session";

// The scope store persists to sessionStorage, which node has not got.
const memory = new Map<string, string>();
(globalThis as unknown as {sessionStorage: unknown}).sessionStorage = {
    getItem: (k: string): string | null => memory.get(k) ?? null,
    setItem: (k: string, v: string): void => void memory.set(k, String(v)),
    removeItem: (k: string): void => void memory.delete(k),
};

// The open model's blobs are read from the scope it was opened from, not from
// whatever scope the storage browser has moved on to since.

afterEach(() => {
    useModelSessionStore.getState().close();
    useScopeStore.setState({current: null});
});

function openFea(sourceName: string, scope?: string): void {
    const session = useModelSessionStore.getState().open({sourceName, kind: "fea"});
    session.fea = {sourceName, scope} as unknown as FeaSessionHandle;
}

test("the open model keeps the scope it was loaded from", () => {
    openFea("model.SIN", "shared");
    useScopeStore.setState({current: {kind: "user", id: "me", name: "Personal"}});
    assert.equal(feaSourceScope("model.SIN"), "shared");
});

test("any other source is read from the scope being browsed", () => {
    openFea("model.SIN", "shared");
    useScopeStore.setState({current: {kind: "user", id: "me", name: "Personal"}});
    assert.equal(feaSourceScope("other.SIN"), "user:me");
    assert.equal(feaSourceScope(null), "user:me");
});

test("a handle without a recorded scope falls back to the browsing scope", () => {
    openFea("model.SIN");
    useScopeStore.setState({current: {kind: "project", id: "p1", name: "P"}});
    assert.equal(feaSourceScope("model.SIN"), "project:p1");
});
