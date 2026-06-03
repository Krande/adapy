import React, { useRef } from 'react';
import TreeViewComponent from './TreeViewComponent';
import { useViewerStores } from "@/state/AdaViewerContext";

// Floating left drawer that holds the selection tree. Toggled from the
// top-bar button (Menu.tsx) and Shift+T (setupCameraControlsHandlers);
// closed via the same controls or the panel's own close button. Always
// overlays the canvas — no canvas reflow on open/close/resize.
const ResizableTreeView: React.FC = () => {
    const isResizing = useRef(false);
    const { useTreeViewStore } = useViewerStores();
    const { isTreeCollapsed, setIsTreeCollapsed, treeViewWidth, setTreeViewWidth } = useTreeViewStore();

    if (isTreeCollapsed) return null;

    const handleMouseDown = (e: React.MouseEvent) => {
        isResizing.current = true;
        const startX = e.clientX;
        const startWidth = treeViewWidth;

        const handleMouseMove = (moveEvent: MouseEvent) => {
            if (!isResizing.current) return;
            const newWidth = Math.min(400, Math.max(200, startWidth + moveEvent.clientX - startX));
            setTreeViewWidth(newWidth);
        };
        const handleMouseUp = () => {
            isResizing.current = false;
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
    };

    return (
        <div
            style={{ width: `${treeViewWidth}px` }}
            className="absolute top-0 left-0 z-20 max-w-[85vw] flex flex-col h-full bg-gray-800 shadow-lg"
        >
            {/* Header with title + close button. The top-bar tree button
                also closes the drawer on desktop, but the in-panel close
                gives mobile users an obvious way back to the viewer when
                the drawer covers most of the screen. */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700 text-white text-sm shrink-0">
                <span className="font-semibold">Selection</span>
                <button
                    type="button"
                    onClick={() => setIsTreeCollapsed(true)}
                    className="text-gray-300 hover:text-white text-xl leading-none px-2 -my-1"
                    aria-label="Close selection tree"
                    title="Close (Shift+T)"
                >
                    ×
                </button>
            </div>
            <div className="flex-1 overflow-auto">
                <TreeViewComponent />
            </div>
            {/* Resize handle — desktop only, no value on touch. */}
            <div
                className="absolute top-0 right-0 w-2 h-full cursor-ew-resize bg-gray-600 hidden md:block"
                onMouseDown={handleMouseDown}
            />
        </div>
    );
};

export default ResizableTreeView;
