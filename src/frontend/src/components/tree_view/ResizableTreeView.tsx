import React, { useEffect, useRef, useState } from 'react';
import AssetBrowser, { AssetBrowserTabs, assetsTabAvailable } from '@/components/asset_browser/AssetBrowser';
import { useViewerStores } from "@/state/AdaViewerContext";

const MIN_WIDTH = 200;
const MAX_WIDTH = 560;

// Floating left drawer that holds the selection tree. Toggled from the
// top-bar button (Menu.tsx) and Shift+T (setupCameraControlsHandlers);
// closed via the same controls or the panel's own close button. Always
// overlays the canvas — no canvas reflow on open/close/resize.
//
// CLOSED IS HIDDEN, NOT UNMOUNTED. Unmounting on close made every open a cold
// start: the Assets tab re-read its collections, re-merged every collection
// index and rebuilt its whole view -- most of a million rows for a project --
// on the main thread before the drawer could paint. The Files tab already stays
// mounted across tab switches for a related reason (see AssetBrowser); the
// drawer now does the same across open/close, so opening only shows it.
const ResizableTreeView: React.FC = () => {
    const isResizing = useRef(false);
    const [dragging, setDragging] = useState(false);
    const { useTreeViewStore } = useViewerStores();
    // Field by field: the whole store would re-render the drawer, and everything
    // in it, on every tree change -- selection included.
    const isTreeCollapsed = useTreeViewStore((s) => s.isTreeCollapsed);
    const setIsTreeCollapsed = useTreeViewStore((s) => s.setIsTreeCollapsed);
    const treeViewWidth = useTreeViewStore((s) => s.treeViewWidth);
    const setTreeViewWidth = useTreeViewStore((s) => s.setTreeViewWidth);
    // Mounted on the FIRST open, then kept: a viewer that never opens the drawer
    // should not pay for loading what is in it.
    const [everOpened, setEverOpened] = useState(!isTreeCollapsed);
    useEffect(() => {
        if (!isTreeCollapsed) setEverOpened(true);
    }, [isTreeCollapsed]);
    if (!everOpened && isTreeCollapsed) return null;

    const handleMouseDown = (e: React.MouseEvent) => {
        e.preventDefault();
        isResizing.current = true;
        setDragging(true);
        const startX = e.clientX;
        const startWidth = treeViewWidth;
        // Held for the drag: the pointer leaves the handle as soon as it moves, and without these
        // the cursor flickers back and the drag selects the tree's text.
        const prevCursor = document.body.style.cursor;
        const prevSelect = document.body.style.userSelect;
        document.body.style.cursor = "ew-resize";
        document.body.style.userSelect = "none";

        const handleMouseMove = (moveEvent: MouseEvent) => {
            if (!isResizing.current) return;
            const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + moveEvent.clientX - startX));
            setTreeViewWidth(newWidth);
        };
        const handleMouseUp = () => {
            isResizing.current = false;
            setDragging(false);
            document.body.style.cursor = prevCursor;
            document.body.style.userSelect = prevSelect;
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
    };

    return (
        <div
            style={{ width: `${treeViewWidth}px` }}
            hidden={isTreeCollapsed}
            aria-hidden={isTreeCollapsed}
            className={`absolute top-0 left-0 z-20 max-w-[85vw] flex-col h-full bg-gray-800 border-r border-gray-700/80 shadow-[4px_0_24px_-4px_rgba(0,0,0,0.55)] ${
                isTreeCollapsed ? "hidden" : "flex"
            }`}
        >
            {/* Header with title + close button. The top-bar tree button
                also closes the drawer on desktop, but the in-panel close
                gives mobile users an obvious way back to the viewer when
                the drawer covers most of the screen. */}
            <div className="flex items-center justify-between h-11 pl-2.5 pr-1.5 border-b border-gray-700/80 text-white text-sm shrink-0">
                {/* With a server the title slot carries the Files | Assets tabs;
                    without one (notebook, websocket) the drawer is unchanged. */}
                {assetsTabAvailable() ? <AssetBrowserTabs /> : <span className="font-semibold px-1">Selection</span>}
                <button
                    type="button"
                    onClick={() => setIsTreeCollapsed(true)}
                    className="grid place-items-center h-7 w-7 rounded-md text-gray-400 hover:text-white hover:bg-white/10"
                    aria-label="Close selection tree"
                    title="Close (Shift+T)"
                >
                    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true">
                        <path d="M4 4l8 8M12 4l-8 8" />
                    </svg>
                </button>
            </div>
            <AssetBrowser />
            {/* Resize handle — desktop only, no value on touch. A wide invisible grab area
                centred on the drawer's edge, drawn as a hairline that lights up when it is
                usable: the edge itself is the border above. */}
            <div
                className="group absolute top-0 -right-1.5 w-3 h-full cursor-ew-resize hidden md:block"
                onMouseDown={handleMouseDown}
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize the selection tree"
            >
                <span
                    className={`absolute inset-y-0 left-1/2 -translate-x-1/2 w-0.5 transition-colors ${
                        dragging ? "bg-blue-400" : "bg-transparent group-hover:bg-blue-400/70"
                    }`}
                />
            </div>
        </div>
    );
};

export default ResizableTreeView;
