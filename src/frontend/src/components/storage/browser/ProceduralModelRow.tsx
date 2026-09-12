import React from "react";
import type {ProceduralModelSummary} from "@/services/viewerApi";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import ProceduralModelIcon from "../../icons/ProceduralModelIcon";

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
    /** This model's compiled result is currently in the scene. */
    isLoaded: boolean;
    /** False until the model has a compiled result to show. */
    canLoad: boolean;
    onToggleLoaded: (model: ProceduralModelSummary, next: boolean) => void;
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
    canLoad,
    onToggleLoaded,
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
        title={`Procedural cell model (${model.name}) — click to open in the cellbuilder`}
    >
        {/* Same meaning as a file's checkbox: put this in the scene. NOT the
            multi-select — that is the row tint, exactly as it is for a file.
            Disabled until the model has been compiled once, because there is
            no result to show and a tickable box with nothing behind it is a
            promise the row cannot keep. */}
        <input
            type="checkbox"
            className="h-5 w-5 shrink-0 cursor-pointer disabled:cursor-not-allowed"
            checked={isLoaded}
            disabled={!canLoad}
            aria-label={isLoaded ? `Unload ${model.name} from scene` : `Load ${model.name} into scene`}
            onClick={(e) => e.stopPropagation()}
            onChange={() => void onToggleLoaded(model, !isLoaded)}
            title={
                !canLoad
                    ? "No compiled result yet — open the model and compile once."
                    : isLoaded
                      ? "Unload from scene (keeps the model)"
                      : "Load the compiled result into the scene, and edit this model"
            }
        />
        <ProceduralModelIcon className="shrink-0"/>
        {/* The LEAF, not the whole path: the folders are already the tree rows
            above it, and repeating them in every label is noise. */}
        <span className="truncate text-sm">{displayName}</span>
        <span className="text-[10px] text-purple-300 border border-purple-400/50 rounded-sm px-1">
            r{model.revision}
        </span>
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
