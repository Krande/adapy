import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

import SimWindowFrame, {DOCKED_AUTO_MAX, DOCKED_MIN, dockedFrameStyle} from "@/components/simulation/SimWindowFrame";

// The docked Simulation panel used to open at a fixed width (or whatever width was
// last dragged to, persisted), however little its controls needed. It now sizes
// to its content within bounds until the user drags its edge.

test("without a user width the docked panel is content-sized and capped", () => {
    const style = dockedFrameStyle(null);
    assert.equal(style.width, "fit-content");
    assert.equal(style.minWidth, `min(${DOCKED_MIN}px, 100%)`);
    assert.equal(style.maxWidth, `min(${DOCKED_AUTO_MAX}px, calc(100vw - 32px), 100%)`);
});

test("a dragged width is used as is, bounded only by the window and container", () => {
    const style = dockedFrameStyle(900);
    assert.equal(style.width, 900);
    assert.equal(style.maxWidth, "min(calc(100vw - 32px), 100%)");
});

test("the docked frame renders content-sized, with its resize handle and floating button", () => {
    const html = renderToStaticMarkup(
        <SimWindowFrame mode="docked" setMode={() => {}}>
            <span>body</span>
        </SimWindowFrame>,
    );
    assert.match(html, /width:fit-content/);
    assert.match(html, /aria-label="Resize simulation panel width"/);
    assert.match(html, /title="Floating window"/);
});
