import React from 'react';
import {NodeRendererProps} from 'react-arborist';

export interface TreeNodeData {
    id: string;
    name: string;
    children: TreeNodeData[];

    // Custom properties
    model_key?: string | null;
    node_name?: string | null;
    rangeId?: string | null;
}

export const CustomNode: React.FC<NodeRendererProps<TreeNodeData>> = ({style, node, dragHandle}) => {
    const {data, isSelected, isOpen, children} = node;


    // data.visuallySelected = false;
    let hasChildren = false;
    if (children && children.length > 0) {
        hasChildren = true;
    }

    return (
        <div
            style={style}
            ref={dragHandle}
            className={`flex items-center cursor-pointer mr-1 my-1 rounded ${
                isSelected ? 'bg-blue-700' : ''
            }`}
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
                    {isOpen ? '▼' : '▶'}
                </div>
            )}
            <div className="whitespace-nowrap">{data.name}</div>
        </div>
    );
};
