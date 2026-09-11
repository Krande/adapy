// The property the whole `AdaViewerContext` exercise exists to unlock: two
// `<AdaViewerProvider>`s on one page get two independent sets of scene-graph
// handles.
//
// While `state/refs.ts` held them as module-level `createRef()`s this was
// unprovable AND untrue — the second viewer to mount overwrote the first one's
// scene, camera and renderer, and every imperative helper then acted on the
// survivor. The assertions below are exactly that failure: mount two providers,
// write a distinct marker into each one's `scene` ref, and require both markers
// to still be there.
//
// Rendered with `react-dom/server`, which needs no DOM — the provider creates
// and registers its runtime during render, precisely so a child reaching an
// imperative helper from its own mount effect already resolves to it.

import assert from "node:assert/strict";
import { test } from "node:test";

// The store modules read `window` at import time (deployment mode, persisted
// settings), so give them a minimal browser-ish realm before pulling the
// provider in. Far short of jsdom, and enough: nothing here touches layout.
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

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider, useViewerRefs } = await import("@/state/AdaViewerContext");
const { createViewerRuntime, getViewerRuntime, hasMountedViewerRuntime, mountViewerRuntime } =
    await import("@/state/viewerRuntime");

type Refs = ReturnType<typeof useViewerRefs>;

/** Renders one provider and hands back the runtime its children see. */
function mountProvider(): Refs {
    let seen: Refs | null = null;
    function Probe() {
        seen = useViewerRefs();
        return null;
    }
    renderToStaticMarkup(
        React.createElement(AdaViewerProvider, null, React.createElement(Probe)),
    );
    assert.ok(seen, "the probe never rendered inside the provider");
    return seen!;
}

test("two providers get two runtimes, and neither can see the other's scene", () => {
    const first = mountProvider();
    const second = mountProvider();

    assert.notEqual(first, second, "both providers handed out the same runtime object");
    assert.notEqual(first.scene, second.scene, "both providers share one scene ref");
    assert.notEqual(first.camera, second.camera, "both providers share one camera ref");

    // The failure the singletons had: the second mount overwrote the first's
    // scene. Markers stand in for THREE objects — nothing here reads them as
    // scenes, and constructing real ones would drag WebGL into a node test.
    const sceneA = { name: "first" } as unknown as NonNullable<Refs["scene"]["current"]>;
    const sceneB = { name: "second" } as unknown as NonNullable<Refs["scene"]["current"]>;
    first.scene.current = sceneA;
    second.scene.current = sceneB;

    assert.equal(first.scene.current, sceneA);
    assert.equal(second.scene.current, sceneB);
});

test("every field of a fresh runtime starts empty and is its own ref", () => {
    const a = createViewerRuntime();
    const b = createViewerRuntime();
    const keys = Object.keys(a) as (keyof typeof a)[];
    assert.equal(keys.length, 10);
    for (const key of keys) {
        assert.equal(a[key].current, null, `${String(key)} did not start null`);
        assert.notEqual(a[key], b[key], `${String(key)} is shared between runtimes`);
    }
});

test("the imperative accessor follows the mount stack", () => {
    // The providers rendered above never unmount (server rendering runs no
    // effects), so start from what they left and put it back at the end.
    const outer = getViewerRuntime();

    const a = createViewerRuntime();
    const b = createViewerRuntime();
    const unmountA = mountViewerRuntime(a);
    assert.equal(getViewerRuntime(), a);
    const unmountB = mountViewerRuntime(b);
    assert.equal(getViewerRuntime(), b, "the newest mounted viewer should answer");

    // Idempotent: a re-entered render must not stack the same runtime twice.
    mountViewerRuntime(b);
    unmountB();
    assert.equal(getViewerRuntime(), a, "unmounting the newer viewer should hand back the older");

    unmountA();
    assert.equal(getViewerRuntime(), outer);
    assert.equal(hasMountedViewerRuntime(), true);
});
