// The attribute search's own floating window: over the viewer, dragged by its title bar, resized
// from any edge or corner, and independent of the in-viewer panel host -- Admin or Convert can be
// open beside it.
//
// Its place and size are remembered per browser and always pass through `clampRect` (on open, and
// whenever the browser window is resized), so a position saved on a larger screen still opens on
// this one. On a phone it takes the screen, as the panel host's windows do.
//
// Drawn in a portal over the page so its coordinates are the viewport's, whatever the layout the
// launcher sits in. The search itself is `AttributeSearchPanel`; its state is the shared store, so
// closing the window loses nothing.

import React, {Suspense, lazy, useEffect, useState} from "react";
import {createPortal} from "react-dom";
import {Rnd} from "react-rnd";

import {clampRect, defaultRect, parseStoredRect, type WindowRect} from "@/assets/floatingWindow";
import {SEARCH_WINDOW_TITLE, useSearchWindowStore} from "@/state/searchWindowStore";

const AttributeSearchPanel = lazy(() => import("./AttributeSearchPanel"));

const RECT_KEY = "ada.assets.attributeSearch.window";
const MIN = {width: 420, height: 320};
const PREFERRED = {width: 1000, height: 640};
const MOBILE_QUERY = "(max-width: 767px)";

const viewport = () => ({width: window.innerWidth, height: window.innerHeight});

function readRect(): WindowRect {
    let stored: WindowRect | null = null;
    try {
        stored = parseStoredRect(globalThis.localStorage?.getItem(RECT_KEY) ?? null);
    } catch {
        // Blocked storage: open where a first open would.
    }
    return clampRect(stored ?? defaultRect(viewport(), PREFERRED, MIN), viewport(), MIN);
}

function writeRect(r: WindowRect): void {
    try {
        globalThis.localStorage?.setItem(RECT_KEY, JSON.stringify(r));
    } catch {
        // Remembered for this page only.
    }
}

function useIsMobile(): boolean {
    const [isMobile, setIsMobile] = useState(() => window.matchMedia(MOBILE_QUERY).matches);
    useEffect(() => {
        const mq = window.matchMedia(MOBILE_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);
    return isMobile;
}

const HEADER_BTN =
    "shrink-0 inline-flex items-center justify-center w-9 h-9 sm:w-7 sm:h-7 rounded-sm border border-gray-600 " +
    "bg-gray-700/60 text-gray-200 hover:bg-gray-600 hover:border-gray-400 hover:text-white";

const AttributeSearchWindow: React.FC = () => {
    const open = useSearchWindowStore((s) => s.open);
    const hide = useSearchWindowStore((s) => s.hide);
    const isMobile = useIsMobile();
    const [rect, setRect] = useState<WindowRect>(readRect);

    // Back on screen when opened and whenever the browser window changes size.
    useEffect(() => {
        if (!open) return;
        const fit = () => setRect((r) => clampRect(r, viewport(), MIN));
        fit();
        window.addEventListener("resize", fit);
        return () => window.removeEventListener("resize", fit);
    }, [open]);

    if (!open) return null;

    const commit = (r: WindowRect) => {
        const c = clampRect(r, viewport(), MIN);
        setRect(c);
        writeRect(c);
    };

    const body = (
        <div className="h-full w-full flex flex-col bg-gray-900 border border-gray-700 sm:rounded-sm shadow-2xl overflow-hidden">
            <div
                className={
                    (isMobile ? "" : "search-window-drag-handle cursor-move ") +
                    "flex items-center gap-2 bg-gray-800 text-gray-100 px-3 py-2 border-b border-gray-700 shrink-0"
                }
            >
                <span className="font-medium text-sm truncate">{SEARCH_WINDOW_TITLE}</span>
                <span className="hidden sm:inline text-xs text-gray-400 truncate">· every provider in this scope</span>
                <button
                    type="button"
                    onClick={hide}
                    title="Close (the results are kept)"
                    aria-label="Close attribute search"
                    className={`ml-auto ${HEADER_BTN}`}
                    onMouseDown={(e) => e.stopPropagation()}
                    onTouchStart={(e) => e.stopPropagation()}
                >
                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                        <path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
                    </svg>
                </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
                <Suspense fallback={<div className="p-4 text-sm text-gray-400">Loading…</div>}>
                    <AttributeSearchPanel />
                </Suspense>
            </div>
        </div>
    );

    if (isMobile) {
        return createPortal(
            <div className="fixed inset-0 z-[61] w-screen overflow-hidden" style={{height: "100dvh"}}>
                {body}
            </div>,
            document.body,
        );
    }

    // A fixed, click-through layer the size of the viewport: the window's coordinates are the
    // viewport's, and it can be dragged anywhere on it.
    return createPortal(
        <div className="fixed inset-0 z-[61] pointer-events-none">
            <Rnd
                className="pointer-events-auto"
                position={{x: rect.x, y: rect.y}}
                size={{width: rect.width, height: rect.height}}
                minWidth={MIN.width}
                minHeight={MIN.height}
                bounds="parent"
                dragHandleClassName="search-window-drag-handle"
                onDragStop={(_e, d) => commit({...rect, x: d.x, y: d.y})}
                onResizeStop={(_e, _dir, ref, _delta, pos) =>
                    commit({x: pos.x, y: pos.y, width: ref.offsetWidth, height: ref.offsetHeight})
                }
            >
                {body}
            </Rnd>
        </div>,
        document.body,
    );
};

export default AttributeSearchWindow;
