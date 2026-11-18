import React from "react";
import type {ProceduralModelSummary} from "@/services/viewerApi";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import ProceduralModelIcon from "../../icons/ProceduralModelIcon";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {sourceLabel} from "@/utils/storage/modelSource";

// The open model's uncommitted edits: the same state the Build toolbar's "unsaved" shows,
// so Storage and the toolbar never disagree on whether there is something to commit.
const DirtyBadge: React.FC = () =>
    useCellBuilderStore((s) => s.dirty) ? (
        <span className="text-[10px] text-amber-400 border border-amber-400/50 rounded-sm px-1" title="Edits not committed yet">
            unsaved
        </span>
    ) : null;

// ──────────────────────────────────────────────────────────────────
// ProceduralModelRow: a procedural model rendered as a leaf of the storage
// tree.
//
// A model is a database row, not a blob, so it deliberately does NOT get a
// FileRow: there is nothing to load into the scene, convert, or select for a
// bulk delete, and offering those controls would promise operations that
// cannot work. In particular it carries no selection checkbox, which is what
// keeps a model out of `selection` and therefore out of every bulk file
// operation — the rows simply cannot be swept in.
//
// What it shares with a file is its PLACE: the name carries the folder path,
// so the tree puts it exactly where an operator filed it.
//
// ONE CONTROL. The checkbox is the whole story of a model's presence: ticked, the
// model is in the scene - as the one the builder edits when no other is, else
// beside it (a companion); unticked, it is out of the scene and out of the
// builder. Clicking the row makes it the edited one. There is no separate
// "open" or "stop editing": those were the checkbox said twice.
const ProceduralModelRow: React.FC<{
    model: ProceduralModelSummary;
    displayName: string;
    indentLevel: number;
    active: boolean;
    onOpen: () => void;
    menuItems: KebabMenuItem[];
    onOpenContextMenu?: (e: React.MouseEvent) => void;
    isSelected: boolean;
    onSelectToggle: (name: string, shiftKey?: boolean) => void;
    rowKey: string;
    focused: boolean;
    /** This model is in the scene: edited, or shown beside the edited one. */
    isLoaded: boolean;
    onToggleLoaded: (model: ProceduralModelSummary, next: boolean) => void;
    /** The key of the workbook the model was made from (utils/storage/modelSource), when
     * it is in storage: named on the row in place of a row of its own, as what Save writes. */
    source?: string | null;
}> = ({
    model,
    displayName,
    indentLevel,
    active,
    onOpen,
    menuItems,
    onOpenContextMenu,
    isSelected,
    onSelectToggle,
    rowKey,
    focused,
    isLoaded,
    onToggleLoaded,
    source = null,
}) => (
    <div
        data-row-key={rowKey}
        className={
            "flex items-center gap-1.5 px-2 py-1 rounded-sm hover:bg-gray-700/50 cursor-pointer " +
            (active ? "bg-blue-900/40 " : "") +
            (isSelected ? "bg-amber-700/30 " : "") +
            (focused ? "ring-1 ring-blue-400/70 " : "") +
            // Loaded but not the one being edited: present in the scene, dimmed
            // so the active model is unambiguous at a glance.
            (isLoaded && !active ? "opacity-60 " : "")
        }
        style={{paddingLeft: `${0.5 + indentLevel * 0.75}rem`}}
        onClick={onOpen}
        onContextMenu={onOpenContextMenu}
        title={`Procedural model (${model.name}) — click to edit it in the builder`}
    >
        {/* Same meaning as a file's checkbox: put this in the scene. NOT the
            multi-select — that is the row tint, exactly as it is for a file.
            Always tickable: a model that has never compiled is still a topology
            the builder draws and edits. */}
        <input
            type="checkbox"
            className="h-5 w-5 shrink-0 cursor-pointer"
            checked={isLoaded}
            aria-label={isLoaded ? `Remove ${model.name} from the scene` : `Show ${model.name} in the scene`}
            onClick={(e) => e.stopPropagation()}
            onChange={() => void onToggleLoaded(model, !isLoaded)}
            title={
                isLoaded
                    ? active
                        ? "Remove from the scene and close it in the builder (the model is kept)"
                        : "Remove from the scene (the model is kept)"
                    : "Show in the scene: edited in the builder, or beside the model being edited"
            }
        />
        <ProceduralModelIcon className="shrink-0"/>
        {/* The LEAF, not the whole path: the folders are already the tree rows
            above it, and repeating them in every label is noise. */}
        <span className="truncate text-sm">{displayName}</span>
        <span className="text-[10px] text-purple-300 border border-purple-400/50 rounded-sm px-1">
            r{model.revision}
        </span>
        {active && <DirtyBadge/>}
        {source && (
            <span
                className="truncate text-[10px] text-gray-400"
                data-model-source={source}
                title={`Made from ${source}; Save writes the model's changes back into it`}
            >
                {sourceLabel(source)}
            </span>
        )}
        {/* The same kebab files and folders carry. An operator filing a model
            beside them should not meet a different control for "move". */}
        <span className="ml-auto shrink-0" onClick={(e) => e.stopPropagation()}>
            <RowKebabMenu
                ariaLabel={`Organize procedural model ${model.name}`}
                buttonClassName="h-6 w-6 text-gray-300 hover:bg-gray-700"
                header={<span className="font-mono">{model.name}</span>}
                items={menuItems}
            />
        </span>
    </div>
);

export default ProceduralModelRow;
