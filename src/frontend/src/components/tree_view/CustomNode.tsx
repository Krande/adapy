import React from 'react';
import {NodeRendererProps} from 'react-arborist';
import {handleClickedNode} from "../../utils/tree_view/handleClickedNode";

interface TreeNodeData {
    id: string;
    name: string;
    children?: TreeNodeData[];
    // Add other properties if needed
}

export const CustomNode: React.FC<NodeRendererProps<TreeNodeData>> = ({style, node, dragHandle}) => {
    const {data, isSelected, isOpen, children} = node;
    let hasChildren = false;
    if (children && children.length > 0) {
        hasChildren = true;
    }

    return (
        <div
            style={style}
            ref={dragHandle}
            className={`flex items-center px-2 py-1 cursor-pointer ${
                isSelected ? 'bg-blue-500' : ''
            }`}
            onClick={(event) => handleClickedNode(event, node.id)}
        >
            {/* Conditionally render the icon */}
            {hasChildren && (
                <div
                    onClick={(e) => {
                        e.stopPropagation(); // Prevent selection when toggling
                        node.toggle();
                    }}
                    className="mr-2"
                >
                    {isOpen ? '-' : '+'}
                </div>
            )}
            <div>{data.name}</div>
        </div>
    );
};
