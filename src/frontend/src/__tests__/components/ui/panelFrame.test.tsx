import assert from "node:assert/strict";
import {test} from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

import {
    CloseGlyph,
    ExternalGlyph,
    FloatGlyph,
    PanelCard,
    PanelFrame,
    PanelHeaderActions,
    PanelHeaderActionsProvider,
    WindowFrame,
    WindowHeaderActions,
    WindowHeaderButton,
} from "@/components/ui/PanelFrame";
import {SheetHandle} from "@/components/ui/SheetHandle";
import {PANEL_CHROME, PANEL_SURFACE, WINDOW_CARD, WINDOW_HEADER, WINDOW_HEADER_BUTTON} from "@/components/ui/panelChrome";

// Every frame here replaced markup a panel used to write itself. Each test renders
// that legacy markup, verbatim from the component it came from, beside the frame,
// and requires the two to be the same string: adopting a frame must not change one
// node, class or attribute.

// The legacy markup these compare against predates the body marker (`PANEL_BODY_ATTR`),
// which draws nothing: it is left out of the comparison, and checked on its own below.
const html = (el: React.ReactElement) => renderToStaticMarkup(el).replaceAll(' data-ada-panel-body=""', "").replace(/ data-ada-panel-id="[^"]*"/g, "");
const noop = () => {};

test("a card and a window's body carry the body marker a shell's palette is scoped to", () => {
    assert.match(renderToStaticMarkup(<PanelCard>x</PanelCard>), /^<div[^>]* data-ada-panel-body=""/);
    assert.match(renderToStaticMarkup(<WindowFrame title="W">x</WindowFrame>), /<div class="flex-1 min-h-0 overflow-hidden" data-ada-panel-body="">x<\/div>/);
});

test("PanelCard draws the menu-column card with its heading", () => {
    const legacy = (
        <div className={`${PANEL_CHROME} min-w-80 max-h-[80svh] overflow-y-auto`}>
            <h2 className="font-bold">Selected Object Info</h2>
            <p>body</p>
        </div>
    );
    const card = (
        <PanelCard className="min-w-80 max-h-[80svh] overflow-y-auto" heading="Selected Object Info">
            <p>body</p>
        </PanelCard>
    );
    assert.equal(html(card), html(legacy));
});

test("PanelCard's surface variant is the bare surface, other props reach the root", () => {
    // (Attribute order in a server render follows prop order; a DOM has none. The
    // legacy element lists `className` first, as the card does.)
    const legacy = (
        <div className={PANEL_SURFACE + " fixed z-40"} data-no-upload-menu role="dialog" aria-label="Simulation" style={{left: 4}}>
            x
        </div>
    );
    const card = (
        <PanelCard variant="surface" data-no-upload-menu role="dialog" aria-label="Simulation" className="fixed z-40" style={{left: 4}}>
            x
        </PanelCard>
    );
    assert.equal(html(card), html(legacy));
});

test("WindowFrame is the in-viewer window's card, title bar and lazy body", () => {
    // InViewerPanelHost before the frame: HeaderBar + the body wrapper (its
    // colours since tokenised: the chrome constants below).
    const legacy = (
        <div className={WINDOW_CARD}>
            <div className={"viewer-panel-drag-handle cursor-move " + WINDOW_HEADER}>
                <span className="font-medium text-sm truncate">Admin panel</span>
                <div className="ml-auto flex items-center gap-1">
                    <button
                        type="button"
                        onClick={noop}
                        title="Open in dedicated tab"
                        aria-label="Open in dedicated tab"
                        className={WINDOW_HEADER_BUTTON}
                    >
                        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                            <path
                                d="M9 2 H14 V7 M14 2 L8 8 M11 9 V13 H3 V5 H7"
                                stroke="currentColor"
                                strokeWidth="1.5"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                fill="none"
                            />
                        </svg>
                    </button>
                    <button
                        type="button"
                        onClick={noop}
                        title="Close"
                        aria-label="Close panel"
                        className={WINDOW_HEADER_BUTTON}
                    >
                        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                            <path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none"/>
                        </svg>
                    </button>
                </div>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
                <p>body</p>
            </div>
        </div>
    );
    const frame = (
        <WindowFrame
            title="Admin panel"
            dragHandleClassName="viewer-panel-drag-handle"
            actions={
                <WindowHeaderActions>
                    <WindowHeaderButton onClick={noop} title="Open in dedicated tab" ariaLabel="Open in dedicated tab">
                        <ExternalGlyph/>
                    </WindowHeaderButton>
                    <WindowHeaderButton onClick={noop} title="Close" ariaLabel="Close panel">
                        <CloseGlyph/>
                    </WindowHeaderButton>
                </WindowHeaderActions>
            }
        >
            <p>body</p>
        </WindowFrame>
    );
    assert.equal(html(frame), html(legacy));
});

test("WindowFrame with a subtitle and a lone close button is the search window's", () => {
    const HEADER_BTN = WINDOW_HEADER_BUTTON;
    // AttributeSearchWindow before the frame, on a phone (no drag handle).
    const legacy = (
        <div className={WINDOW_CARD}>
            <div className={"" + WINDOW_HEADER}>
                <span className="font-medium text-sm truncate">Search</span>
                <span className="hidden sm:inline text-xs text-gray-400 truncate">· every provider in this scope</span>
                <button
                    type="button"
                    onClick={noop}
                    title="Close (the results are kept)"
                    aria-label="Close attribute search"
                    className={`ml-auto ${HEADER_BTN}`}
                >
                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                        <path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
                    </svg>
                </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
                <p>results</p>
            </div>
        </div>
    );
    const frame = (
        <WindowFrame
            title="Search"
            subtitle="· every provider in this scope"
            dragHandleClassName={null}
            actions={
                <WindowHeaderButton onClick={noop} title="Close (the results are kept)" ariaLabel="Close attribute search" className="ml-auto">
                    <CloseGlyph/>
                </WindowHeaderButton>
            }
        >
            <p>results</p>
        </WindowFrame>
    );
    assert.equal(html(frame), html(legacy));
});

test("SheetHandle is the grab handle the sheets drew", () => {
    const legacy = (
        <div
            className="sm:hidden shrink-0 flex justify-center items-center py-2 cursor-grab active:cursor-grabbing touch-none"
            role="separator"
            aria-label="Drag to resize the panel"
        >
            <span className="block w-10 h-1.5 rounded-full bg-gray-400/70" aria-hidden="true" />
        </div>
    );
    const grab = {onPointerDown: noop, onPointerMove: noop, onPointerUp: noop, onPointerCancel: noop};
    assert.equal(html(<SheetHandle grab={grab} className="sm:hidden" />), html(legacy));
    assert.match(html(<SheetHandle grab={grab} />), /^<div class="shrink-0 flex justify-center/);
});

test("PanelFrame draws a window frame in a floating host, with the host's actions and close", () => {
    const out = html(
        <PanelFrame
            panelId="core:x"
            title="Things"
            host={{kind: "float", zone: "classic.float"}}
            actions={[{id: "dock", label: "Dock", run: noop}]}
            onClose={noop}
            dragHandleClassName="my-handle"
        >
            <p>body</p>
        </PanelFrame>,
    );
    assert.match(out, /class="my-handle cursor-move flex items-center/);
    assert.match(out, /title="Dock" aria-label="Dock"/);
    assert.match(out, /title="Close" aria-label="Close panel"/);
    assert.match(out, /<p>body<\/p>/);
});

test("PanelFrame draws a card with its title in a stack, and no buttons without actions", () => {
    const out = html(
        <PanelFrame panelId="core:x" title="Things" host={{kind: "stack", zone: "classic.menu-column"}} actions={[]}>
            <p>body</p>
        </PanelFrame>,
    );
    assert.ok(out.startsWith(`<div class="${PANEL_CHROME}" data-panel-id="core:x">`));
    assert.match(out, /<h2 class="font-bold">Things<\/h2>/);
    assert.doesNotMatch(out, /<button/);
});

test("a header's action slot is empty until a host offers actions", () => {
    const card = (
        <PanelCard heading="Options">
            <p>body</p>
        </PanelCard>
    );
    const bare = html(card);
    assert.equal(html(<PanelHeaderActionsProvider actions={[]}>{card}</PanelHeaderActionsProvider>), bare);
    assert.equal(html(<div><PanelHeaderActions /></div>), "<div></div>");

    const offered = html(
        <PanelHeaderActionsProvider actions={[{id: "float", label: "Float in a window", icon: FloatGlyph, run: noop}]}>
            {card}
        </PanelHeaderActionsProvider>,
    );
    assert.match(offered, /<div class="flex items-center gap-2"><h2 class="font-bold">Options<\/h2><div class="ml-auto/);
    assert.match(offered, /title="Float in a window" aria-label="Float in a window"/);
});

test("an action with a menu is a closed menu button; a docked card's header carries the drag handle and names its panel", () => {
    const out = html(
        <PanelFrame
            panelId="core:x"
            title="Things"
            host={{kind: "dock", zone: "right"}}
            actions={[{id: "move", label: "Move", run: noop, menu: [{id: "a", label: "A", run: noop}]}]}
            dragHandleClassName="my-handle"
        >
            <p>body</p>
        </PanelFrame>,
    );
    assert.match(out, /<div class="flex items-center gap-2 my-handle"><h2/);
    assert.match(out, /data-panel-actions="core:x"/);
    assert.match(out, /aria-label="Move" aria-haspopup="menu" aria-expanded="false"/);
    assert.doesNotMatch(out, /role="menu"/);
});