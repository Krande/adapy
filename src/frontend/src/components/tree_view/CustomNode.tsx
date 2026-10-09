import React from 'react';
import {NodeRendererProps} from 'react-arborist';

import {useVisibilityStore} from '@/state/visibilityStore';
import {hideRows, RowVisibility, rowVisibility, showRows} from '@/utils/tree_view/sceneTreeActions';
import {Chevron, EyeGlyph, ModelGlyph, NodeGlyph} from './treeGlyphs';

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

const VISIBILITY_TITLE: Record<RowVisibility, string> = {
    shown: "Visible -- click to hide",
    hidden: "Hidden -- click to show",
    partial: "Partly hidden -- click to show all of it",
    none: "",
};

export const CustomNode: React.FC<NodeRendererProps<TreeNodeData>> = ({style, node, dragHandle}) => {
    const {data, isSelected, isOpen, children} = node;
    const hasChildren = !!children && children.length > 0;
    // A row with its own draw range is a piece of geometry; one without is a level. The top row of
    // each loaded model is the model.
    const geometry = data.rangeId != null && !!data.node_name;
    const version = useVisibilityStore((s) => s.version);
    const visibility = rowVisibility(data, version, node.tree.props.data);
    const hidden = visibility === "hidden";

    return (
        <div
            style={style}
            ref={dragHandle}
            className={`group flex items-center gap-1.5 cursor-pointer mr-1 my-1 pr-1 rounded ${
                isSelected ? 'bg-blue-700' : 'hover:bg-white/5'
            }`}
            title={hidden ? `${data.name} (hidden)` : undefined}
        >
            <span
                className={`w-3 shrink-0 grid place-items-center ${isSelected ? 'text-gray-100' : 'text-gray-400'}`}
                onClick={(e) => {
                    if (!hasChildren) return;
                    e.stopPropagation(); // Prevent selection when toggling
                    node.toggle();
                }}
            >
                {hasChildren && <Chevron open={isOpen}/>}
            </span>
            <span className={`${isSelected ? 'text-gray-100' : 'text-gray-400'} ${hidden ? 'opacity-50' : ''}`}>
                {node.level === 0 ? <ModelGlyph/> : <NodeGlyph branch={!geometry}/>}
            </span>
            <span className={`whitespace-nowrap flex-1 min-w-0 ${hidden ? 'opacity-50 italic' : ''}`}>{data.name}</span>
            {visibility !== "none" && (
                <button
                    type="button"
                    title={VISIBILITY_TITLE[visibility]}
                    aria-label={VISIBILITY_TITLE[visibility]}
                    tabIndex={-1}
                    // Shown or not, the eye is the toggle; it shows on hover while all is visible, and
                    // stays put once something below is hidden, so a hidden row is visible at a glance.
                    className={`shrink-0 grid place-items-center rounded-sm px-0.5 hover:bg-white/10 ${
                        visibility === "shown" ? 'opacity-0 group-hover:opacity-70' : visibility === "partial" ? 'opacity-60' : 'opacity-90'
                    }`}
                    onClick={(e) => {
                        e.stopPropagation();
                        if (visibility === "shown") hideRows([data]);
                        else showRows([data]);
                    }}
                >
                    <EyeGlyph shut={visibility !== "shown"}/>
                </button>
            )}
        </div>
    );
};
