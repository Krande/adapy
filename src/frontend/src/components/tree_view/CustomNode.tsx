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
    /** On a model's ROOT row only: the source name it was loaded under -- the handle an unload
     *  carries, so the row goes with the model even if the model-key lookup finds nothing. */
    source_name?: string | null;
    /** On a ROOT row: what the model calls its top, and the unique id it was loaded under -- the two
     *  names the Scene tree can show for it (`@/utils/tree_view/rootLabels`). */
    top_name?: string;
    source_label?: string;
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
