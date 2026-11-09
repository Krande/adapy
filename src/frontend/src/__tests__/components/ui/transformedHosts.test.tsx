import assert from "node:assert/strict";
import {afterEach, test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

// The windows that move by a transform (the in-viewer admin / convert window, the
// attribute search, a float layer's default window) mark their
// content as a `TransformedHost`, so the fixed overlays inside it (`OverlayPortal`:
// the admin panel's dialogs and menus) portal out to the page.

const g = globalThis as {window?: unknown; localStorage?: unknown};

function stubWindow() {
    g.window = {
        innerWidth: 1440,
        innerHeight: 900,
        location: new URL("http://localhost/"),
        matchMedia: () => ({matches: false, addEventListener() {}, removeEventListener() {}}),
        addEventListener() {},
        removeEventListener() {},
    };
    g.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};
}

afterEach(() => {
    delete g.window;
    delete g.localStorage;
});

const {OverlayPortal, useInTransformedHost} = await import("@/components/ui/OverlayPortal");

/** Says whether it is drawn inside a transformed host. */
const Probe: React.FC = () => <i>{useInTransformedHost() ? "transformed" : "in-place"}</i>;

test("outside a transformed host, an overlay renders in place", () => {
    assert.equal(renderToStaticMarkup(<Probe/>), "<i>in-place</i>");
    assert.equal(renderToStaticMarkup(<OverlayPortal><Probe/></OverlayPortal>), "<i>in-place</i>");
});

test("the in-viewer admin / convert window is a transformed host", async () => {
    stubWindow();
    const {ViewerWindow} = await import("@/components/ViewerWindow");
    const out = renderToStaticMarkup(
        <ViewerWindow title="Admin panel" onExternal={null} onClose={() => {}}>
            <Probe/>
        </ViewerWindow>,
    );
    assert.match(out, /<i>transformed<\/i>/);
});

// The attribute search's window is drawn in a portal of its own (`overlay`), which the
// server renderer cannot draw; the browser check covers it.

test("a float layer's default window is a transformed host", async () => {
    stubWindow();
    const {DefaultWindowChrome} = await import("@/layout/FloatLayer");
    const panel = {
        id: "test:panel",
        title: "Test",
        capabilities: {closable: true},
        placement: {},
        constraints: {},
    } as unknown as React.ComponentProps<typeof DefaultWindowChrome>["panel"];
    const out = renderToStaticMarkup(
        <DefaultWindowChrome panel={panel} close={() => {}}>
            <Probe/>
        </DefaultWindowChrome>,
    );
    assert.match(out, /<i>transformed<\/i>/);
});
