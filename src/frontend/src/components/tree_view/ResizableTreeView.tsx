import React, { useRef } from 'react';
import TreeViewComponent from './TreeViewComponent';
import { useTreeViewStore } from "@/state/treeViewStore";
import { useNodeEditorStore } from "@/state/useNodeEditorStore";

const ResizableTreeView: React.FC = () => {
    const isResizing = useRef(false);
    const { isTreeCollapsed, setIsTreeCollapsed, treeViewWidth, setTreeViewWidth } = useTreeViewStore();
    const { use_node_editor_only } = useNodeEditorStore();

    // Handle mouse down event on the resize handle
    const handleMouseDown = (e: React.MouseEvent) => {
        if (isTreeCollapsed) return; // Prevent resizing if collapsed
        isResizing.current = true;

        const startX = e.clientX;
        const startWidth = treeViewWidth;

        // Handle mouse move event to resize the tree view
        const handleMouseMove = (moveEvent: MouseEvent) => {
            if (!isResizing.current) return;
            const newWidth = Math.min(
                400, // Max width
                Math.max(200, startWidth + moveEvent.clientX - startX) // Min width
            );
            setTreeViewWidth(newWidth);
        };

        // Handle mouse up event to stop resizing
        const handleMouseUp = () => {
            isResizing.current = false;
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
    };

    return (
        <div className="relative flex h-full">
            {/* Tree View Section.
                Always floats over the canvas (mobile and desktop) so the
                3D view doesn't reflow when the panel opens/closes/resizes.
                Reflowing the canvas resizes the WebGL context which
                visually "jumps" the camera and re-fits the model — bad UX
                while you're navigating. The desktop drag handle still
                resizes the panel itself, just non-destructively. */}
            {!isTreeCollapsed && (
                <div
                    style={{ width: `${treeViewWidth}px` }}
                    className="absolute top-0 left-0 z-20 max-w-[85vw] flex-grow-0 flex-shrink-0 h-full bg-gray-800 overflow-auto shadow-lg"
                >
                    {/* The actual tree view component */}
                    <TreeViewComponent />

                    {/* Resize Handle — desktop only, no value on touch. */}
                    <div
                        className="absolute top-0 right-0 w-2 h-full cursor-ew-resize bg-gray-600 hidden md:block"
                        onMouseDown={handleMouseDown}
                    />
                </div>
            )}

            {/* Tree toggle tab — vertical leaf on the left edge of the
                viewport when closed, on the right edge of the panel when
                open. Replaces the dedicated tree button in the top bar so
                the top bar stays compact on mobile. */}
            {!use_node_editor_only && (
                <button
                    type="button"
                    onClick={() => setIsTreeCollapsed(!isTreeCollapsed)}
                    style={{ left: isTreeCollapsed ? 0 : `min(${treeViewWidth}px, 85vw)` }}
                    className="absolute top-1/2 -translate-y-1/2 z-30 bg-blue-700 hover:bg-blue-600 text-white py-3 px-1 rounded-r shadow-lg transition-[left] duration-150 select-none"
                    aria-label={isTreeCollapsed ? 'Show tree' : 'Hide tree'}
                    title={isTreeCollapsed ? 'Show tree' : 'Hide tree'}
                >
                    {isTreeCollapsed ? '›' : '‹'}
                </button>
            )}
        </div>
    );
};

export default ResizableTreeView;
