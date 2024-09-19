import React, { useState, useRef } from 'react';
import TreeViewComponent from './TreeViewComponent';

const ResizableTreeView: React.FC = () => {
    const [treeViewWidth, setTreeViewWidth] = useState(256); // Initial width of 256px
    const [isCollapsed, setIsCollapsed] = useState(false); // Track whether the tree view is collapsed
    const isResizing = useRef(false);

    // Handle mouse down event on the resize handle
    const handleMouseDown = (e: React.MouseEvent) => {
        if (isCollapsed) return; // Prevent resizing if collapsed
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
        <div className="relative flex flex-col h-full">
            {/* Collapse Button */}
            <button
                className="absolute bottom-2 left-14 z-10 bg-blue-700 text-white p-1 rounded focus:outline-none"
                onClick={() => setIsCollapsed(!isCollapsed)}
            >
                {isCollapsed ? '▶' : '◀'}
            </button>

            {/* Tree View Section */}
            {!isCollapsed && (
                <div
                    style={{ width: `${treeViewWidth}px` }}
                    className="flex-grow-0 flex-shrink-0 bg-gray-800 overflow-auto relative"
                >
                    {/* The actual tree view component */}
                    <TreeViewComponent />

                    {/* Resize Handle */}
                    <div
                        className="absolute top-0 right-0 w-2 h-full cursor-ew-resize bg-gray-600"
                        onMouseDown={handleMouseDown}
                    />
                </div>
            )}
        </div>
    );
};

export default ResizableTreeView;
