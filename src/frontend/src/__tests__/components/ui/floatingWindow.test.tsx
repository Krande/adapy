import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {Rnd} from "react-rnd";

import {FloatingWindow, readRememberedRect, writeRememberedRect} from "@/components/ui/FloatingWindow";

// `FloatingWindow` replaced three hand-written react-rnd wrappers. Each test renders
// the wrapper a window used to write beside the shared component configured as that
// window, and requires the same markup.

const g = globalThis as {window?: unknown; localStorage?: unknown};

function stubWindow(narrow: boolean, store: Record<string, string> = {}) {
    g.window = {
        innerWidth: 1440,
        innerHeight: 900,
        matchMedia: () => ({matches: narrow, addEventListener() {}, removeEventListener() {}}),
        addEventListener() {},
        removeEventListener() {},
    };
    g.localStorage = {
        getItem: (k: string) => (k in store ? store[k] : null),
        setItem: (k: string, v: string) => {
            store[k] = v;
        },
    };
    return store;
}

afterEach(() => {
    delete g.window;
    delete g.localStorage;
});

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

test("a fixed first rect is the node editor's uncontrolled window", () => {
    const legacy = (
        <Rnd
            default={{x: 100, y: 100, width: 800, height: 600}}
            bounds="window"
            style={{zIndex: 1000, background: "white", border: "1px solid #ccc"}}
            dragHandleClassName="node-editor-drag-handle"
        >
            <p>graph</p>
        </Rnd>
    );
    const shared = (
        <FloatingWindow
            dragHandleClassName="node-editor-drag-handle"
            zIndex={1000}
            initialRect={{x: 100, y: 100, width: 800, height: 600}}
            style={{background: "white", border: "1px solid #ccc"}}
        >
            <p>graph</p>
        </FloatingWindow>
    );
    assert.equal(html(shared), html(legacy));
});

test("with a minimum size it is the in-viewer window on a wide screen", () => {
    stubWindow(false);
    const rect = {x: 170, y: 60, width: 1100, height: 720};
    const legacy = (
        <Rnd default={rect} minWidth={420} minHeight={320} bounds="window" style={{zIndex: 60}} dragHandleClassName="viewer-panel-drag-handle">
            <p>admin</p>
        </Rnd>
    );
    const shared = (
        <FloatingWindow
            dragHandleClassName="viewer-panel-drag-handle"
            zIndex={60}
            zClassName="z-[60]"
            minSize={{width: 420, height: 320}}
            initialRect={() => rect}
            fullScreenWhenNarrow
        >
            <p>admin</p>
        </FloatingWindow>
    );
    assert.equal(html(shared), html(legacy));
});

test("below the wide breakpoint it takes the screen, and tells its content so", () => {
    stubWindow(true);
    const legacy = (
        <div className="fixed inset-0 z-[60] w-screen overflow-hidden" style={{height: "100dvh"}}>
            <p>full</p>
        </div>
    );
    const shared = (
        <FloatingWindow dragHandleClassName="h" zIndex={60} zClassName="z-[60]" initialRect={{x: 0, y: 0, width: 1, height: 1}} fullScreenWhenNarrow>
            {({fullScreen}) => <p>{fullScreen ? "full" : "windowed"}</p>}
        </FloatingWindow>
    );
    assert.equal(html(shared), html(legacy));
});

test("a window that does not take the screen stays a window when narrow", () => {
    stubWindow(true);
    const out = html(
        <FloatingWindow dragHandleClassName="h" zIndex={5} initialRect={{x: 1, y: 2, width: 300, height: 200}}>
            {({fullScreen}) => <p>{fullScreen ? "full" : "windowed"}</p>}
        </FloatingWindow>,
    );
    assert.match(out, /windowed/);
    assert.match(out, /translate\(1px, ?2px\)/);
});

test("a remembered window opens where it was left, back on screen", () => {
    const store = stubWindow(false, {
        "ada.test.window": JSON.stringify({x: 2000, y: 40, width: 600, height: 400}),
    });
    const remember = {key: "ada.test.window", preferred: {width: 1000, height: 640}};
    // saved on a wider screen: moved back so its right edge is the viewport's
    assert.deepEqual(readRememberedRect(remember, {width: 420, height: 320}), {x: 840, y: 40, width: 600, height: 400});
    writeRememberedRect(remember, {x: 1, y: 2, width: 500, height: 400});
    assert.deepEqual(JSON.parse(store["ada.test.window"]), {x: 1, y: 2, width: 500, height: 400});
});

test("a window remembered nowhere yet opens at its preferred size, centred near the top", () => {
    stubWindow(false);
    const remember = {key: "ada.none", preferred: {width: 1000, height: 640}};
    assert.deepEqual(readRememberedRect(remember, {width: 420, height: 320}), {x: 220, y: 60, width: 1000, height: 640});
});

test("blocked storage opens where a first open would, and a write is dropped quietly", () => {
    stubWindow(false);
    g.localStorage = {
        getItem() {
            throw new Error("blocked");
        },
        setItem() {
            throw new Error("blocked");
        },
    };
    const remember = {key: "k", preferred: {width: 1000, height: 640}};
    assert.deepEqual(readRememberedRect(remember, {width: 420, height: 320}), {x: 220, y: 60, width: 1000, height: 640});
    assert.doesNotThrow(() => writeRememberedRect(remember, {x: 0, y: 0, width: 500, height: 400}));
});
