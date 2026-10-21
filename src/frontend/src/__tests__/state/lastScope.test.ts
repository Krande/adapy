// The scope a user last browsed, recalled by a tab that has none of its own.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const memory = new Map<string, string>();
const storage = {
    getItem: (k: string) => (memory.has(k) ? memory.get(k)! : null),
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
    clear: () => memory.clear(),
    key: () => null,
    get length() {
        return memory.size;
    },
};
const globals = globalThis as unknown as {sessionStorage: unknown; localStorage: unknown};
globals.sessionStorage = storage;
globals.localStorage = storage;

const {useScopeStore} = await import("../../state/scopeStore");
const {followScope, recallScope, rememberScope} = await import("../../state/lastScope");

const shared = {kind: "shared" as const, id: null, name: "Shared"};
const personal = {kind: "user" as const, id: "me", name: "My files"};
const project = {kind: "project" as const, id: "p1", name: "P1"};

beforeEach(() => {
    memory.clear();
    useScopeStore.setState({current: null, available: []});
});

test("a tab without a scope starts in the one recalled", () => {
    useScopeStore.getState().setAvailable([personal, shared, project], "shared");
    assert.deepEqual(useScopeStore.getState().current, shared);
});

test("the tab's own scope wins over the recalled one", () => {
    useScopeStore.setState({current: project});
    useScopeStore.getState().setAvailable([personal, shared, project], "shared");
    assert.deepEqual(useScopeStore.getState().current, project);
});

test("a recalled scope no longer offered falls back to the first", () => {
    useScopeStore.getState().setAvailable([personal, shared], "project:gone");
    assert.deepEqual(useScopeStore.getState().current, personal);
});

test("the remembered scope belongs to its user only", () => {
    rememberScope("alice", shared);
    assert.equal(recallScope("alice"), "shared");
    assert.equal(recallScope("bob"), null);
    assert.equal(recallScope(null), null);
});

test("followScope remembers each switch", () => {
    useScopeStore.setState({current: personal});
    followScope("alice");
    assert.equal(recallScope("alice"), "user:me");
    useScopeStore.getState().setCurrent(project);
    assert.equal(recallScope("alice"), "project:p1");
});
