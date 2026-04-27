import React, { useState, useRef } from 'react';
import TreeViewComponent from './TreeViewComponent';
import { useTreeViewStore } from "@/state/treeViewStore";

const ResizableTreeView: React.FC = () => {
    const [treeViewWidth, setTreeViewWidth] = useState(256); // Initial width of 256px
    const isResizing = useRef(false);
    const { isTreeCollapsed } = useTreeViewStore();

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
                On mobile (< md) the panel floats over the canvas instead of
                pushing it aside — there's not enough horizontal room to
                shrink the 3D view and still see anything. On desktop it
                stays inline so the user can resize the split. */}
            {!isTreeCollapsed && (
                <div
                    style={{ width: `${treeViewWidth}px` }}
                    className="absolute md:relative top-0 left-0 z-20 max-w-[85vw] flex-grow-0 flex-shrink-0 h-full bg-gray-800 overflow-auto shadow-lg md:shadow-none md:max-w-none"
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
        </div>
    );
};

export default ResizableTreeView;
