// Several paradoc-embedded viewers on one page, each with its own state.
//
// The viewer's stores are still process singletons, so before the embed's
// instance manager every mounted viewer shared one set of them: the newest
// viewer took the stores over on mount, and every other panel's controls then
// drove the newest viewer's model. `embed/viewerInstances` parks each inactive
// viewer's store state and swaps it back in when the user turns to it. These
// are the properties that has to hold: a viewer's settings survive a switch
// away and back, a switch does not leak one viewer's state into another, a new
// viewer starts from scratch, the imperative runtime follows the active viewer,
// and disposing the active viewer leaves nothing of it behind.

import assert from "node:assert/strict";
import { test } from "node:test";

// Minimal browser-ish realm for the store modules (same shim as
// viewerRuntime.multiInstance.test.tsx).
const g = globalThis as Record<string, unknown>;
const cells = new Map<string, string>();
const storage = {
    getItem: (k: string) => (cells.has(k) ? cells.get(k)! : null),
    setItem: (k: string, v: string) => void cells.set(k, String(v)),
    removeItem: (k: string) => void cells.delete(k),
    clear: () => cells.clear(),
    key: () => null,
    length: 0,
};
g.window = {
    location: new URL("http://localhost/"),
    localStorage: storage,
    sessionStorage: storage,
    devicePixelRatio: 1,
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
g.localStorage = storage;
g.sessionStorage = storage;
g.document = {
    documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {} } },
    createElement: () => ({ style: {}, getContext: () => null }),
    addEventListener() {},
    removeEventListener() {},
};

const { createViewerRuntime, getViewerRuntime } = await import("@/state/viewerRuntime");
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { getFeaAnimationPhase, setFeaAnimationPhase } = await import(
    "@/utils/scene/fea/feaAnimationDriver"
);
const {
    createEmbedInstance,
    disposeEmbedInstance,
    isActiveInstance,
    parkedState,
    withActiveInstance,
} = await import("../../../embed/viewerInstances");

test("each embedded viewer keeps its own store state across switches", async () => {
    const changes: string[] = [];
    const a = createEmbedInstance(createViewerRuntime(), (on) => changes.push(`a:${on}`));
    const b = createEmbedInstance(createViewerRuntime(), (on) => changes.push(`b:${on}`));

    // Viewer A: open the tree, start its mode playing, advance its phase.
    await withActiveInstance(a, () => {
        useTreeViewStore.getState().setIsTreeCollapsed(false);
        useFeaAnimationStore.getState().setIsPlaying(true);
        setFeaAnimationPhase(1.25);
    });
    assert.equal(getViewerRuntime(), a.runtime, "the runtime follows the active viewer");

    // Viewer B starts from the stores' initial state, not from A's.
    await withActiveInstance(b, () => {
        assert.equal(useTreeViewStore.getState().isTreeCollapsed, true, "B inherited A's open tree");
        assert.equal(useFeaAnimationStore.getState().isPlaying, false, "B inherited A's play state");
        assert.equal(getFeaAnimationPhase(), 0, "B inherited A's phase");
        useFeaAnimationStore.getState().setStepIndex(3);
    });
    assert.equal(getViewerRuntime(), b.runtime);
    assert.ok(isActiveInstance(b) && !isActiveInstance(a));

    // A, parked, still holds what it had -- which is what its own render loop
    // animates from while B is the active one.
    const aFea = parkedState<{ isPlaying: boolean; stepIndex: number }>(a, "useFeaAnimationStore");
    assert.equal(aFea?.isPlaying, true);
    assert.equal(aFea?.stepIndex, 0, "B's mode change leaked into A");

    // Back to A: its state returns, B's does not come with it.
    await withActiveInstance(a, () => {
        assert.equal(useTreeViewStore.getState().isTreeCollapsed, false);
        assert.equal(useFeaAnimationStore.getState().isPlaying, true);
        assert.equal(useFeaAnimationStore.getState().stepIndex, 0);
        assert.equal(getFeaAnimationPhase(), 1.25);
    });
    assert.equal(getViewerRuntime(), a.runtime);
    assert.deepEqual(changes, ["a:true", "a:false", "b:true", "b:false", "a:true"]);

    // Disposing the active viewer returns the stores to their initial state.
    disposeEmbedInstance(a);
    assert.equal(isActiveInstance(a), false);
    assert.equal(useFeaAnimationStore.getState().isPlaying, false);
    assert.equal(useTreeViewStore.getState().isTreeCollapsed, true);
    assert.equal(getViewerRuntime(), b.runtime, "the survivor is what imperative callers get");

    // B, activated again, still has its own mode.
    await withActiveInstance(b, () => {
        assert.equal(useFeaAnimationStore.getState().stepIndex, 3);
    });
    disposeEmbedInstance(b);
});

test("an activation waits for a viewer's load to finish", async () => {
    const a = createEmbedInstance(createViewerRuntime());
    const b = createEmbedInstance(createViewerRuntime());
    const order: string[] = [];
    let release!: () => void;
    const loading = withActiveInstance(a, async () => {
        order.push("a load start");
        await new Promise<void>((r) => (release = r));
        // Still A's turn: nothing swapped B's state in mid-load.
        assert.ok(isActiveInstance(a));
        order.push("a load end");
    });
    const turn = withActiveInstance(b, () => void order.push("b active"));
    await new Promise((r) => setTimeout(r, 10));
    release();
    await Promise.all([loading, turn]);
    assert.deepEqual(order, ["a load start", "a load end", "b active"]);
    disposeEmbedInstance(a);
    disposeEmbedInstance(b);
});
