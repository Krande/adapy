import React from 'react';
import {NodeRendererProps} from 'react-arborist';

import {isMirroredWindow, useSceneActions} from '@/layout/sceneActions';
import {ROW_VISIBILITY_NAMES} from '@/layout/sceneTreeMirror';
import {useSceneMirrorStore} from '@/state/sceneMirrorStore';
import {useVisibilityStore} from '@/state/visibilityStore';
import {RowVisibility, rowVisibility} from '@/utils/tree_view/sceneTreeActions';
import {Chevron, EyeGlyph, ModelGlyph, NodeGlyph} from './treeGlyphs';

/**
 * A row's visibility: read from the 3D batches in the main window; in a pop-out
 * window, the main window's (`core:scene-tree`, a code per row; none = shown).
 */
function useRowVisibility(data: TreeNodeData, tree: unknown): RowVisibility {
    const version = useVisibilityStore((s) => s.version);
    const mirrored = isMirroredWindow();
    const code = useSceneMirrorStore((s) => (mirrored ? s.visibility.get(data.id) ?? 0 : 0));
    return mirrored ? ROW_VISIBILITY_NAMES[code] ?? "shown" : rowVisibility(data, version, tree);
}

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
    /** Muted text at the row's right edge: a count or a kind that qualifies the name. */
    meta?: string;
    /** A row that only reports something (a count, a kind): drawn muted, and selecting it acts
     *  on nothing -- e.g. an FEA node set, which names no faces to isolate. */
    informational?: boolean;
    /** Drawn with an accent: a row whose state is on, e.g. an FEA set that is isolated. */
    marked?: boolean;
    /** The row's tooltip. */
    title?: string;
}

/** What a host adds to the Scene tree's rows (`SceneTreeProps.rowExtras` / `rowClassName`). */
export interface SceneTreeRowOptions {
    /** Drawn at the row's end, before its eye: a host's badge, a property chip. */
    rowExtras?: (row: TreeNodeData) => React.ReactNode;
    /** Classes added to the row (a host's emphasis for some rows). */
    rowClassName?: (row: TreeNodeData) => string | undefined;
}

export const SceneTreeRowContext = React.createContext<SceneTreeRowOptions>({});

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
    const visibility = useRowVisibility(data, node.tree.props.data);
    const actions = useSceneActions();
    const hidden = visibility === "hidden";
    const {rowExtras, rowClassName} = React.useContext(SceneTreeRowContext);
    const extraClass = rowClassName?.(data);

    return (
        <div
            style={style}
            ref={dragHandle}
            className={`group flex items-center gap-1.5 cursor-pointer mr-1 my-1 pr-1 rounded ${
                isSelected ? 'bg-blue-700' : 'hover:bg-white/5'
            }${extraClass ? ` ${extraClass}` : ''}`}
            title={hidden ? `${data.name} (hidden)` : data.title}
            data-informational={data.informational ? "true" : undefined}
            data-marked={data.marked ? "true" : undefined}
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
            <span
                className={`whitespace-nowrap flex-1 min-w-0 ${hidden ? 'opacity-50 italic' : ''}${
                    data.informational && !isSelected ? ' text-gray-400' : ''
                }${data.marked ? ' font-semibold text-sky-300' : ''}`}
            >
                {data.marked && <span aria-label="isolated" className="mr-1 text-sky-400">●</span>}
                {data.name}
            </span>
            {data.meta && (
                <span className={`shrink-0 pl-2 text-[10px] tabular-nums ${isSelected ? 'text-gray-200' : 'text-gray-400'}`}>
                    {data.meta}
                </span>
            )}
            {rowExtras?.(data)}
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
                        if (visibility === "shown") actions.hideRows([data]);
                        else actions.showRows([data]);
                    }}
                >
                    <EyeGlyph shut={visibility !== "shown"}/>
                </button>
            )}
        </div>
    );
};
