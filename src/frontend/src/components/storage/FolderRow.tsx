import React, {useState} from "react";
import ViewIcon from "../icons/ViewIcon";
import FolderClosedIcon from "../icons/FolderClosedIcon";
import FolderOpenIcon from "../icons/FolderOpenIcon";
import ChevronRightIcon from "../icons/ChevronRightIcon";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import InlineNameInput from "@/components/common/InlineNameInput";
import {KebabMenuItem} from "@/components/common/PositionedMenu";
import {KEYS_MIME, FOLDER_MIME, type ServerFolderNode} from "./storageHelpers";


// Custom drag MIME for in-panel file moves. OS-file drops arrive as
// ``dataTransfer.files`` instead; checking for this type tells the two
// apart (types are readable during dragover, the payload only on drop).
// One folder row in the storage tree. Moved verbatim out of StorageBrowser.
export interface FolderRowProps {
    folder: ServerFolderNode;
    depth: number;
    expanded: boolean;
    fileCount: number;
    /** Client-side pending folder (no server keys under it yet). */
    isPending?: boolean;
    /** Loaded-in-scene models anywhere under this folder — propagates
     * the row-level eye marker up the tree so collapsed folders still
     * show where the loaded models live. */
    loadedCount?: number;
    onToggle: () => void;
    /** Shared with the right-click context menu — built once by the
     * parent so kebab and context menu never diverge. Empty array
     * hides the kebab. */
    menuItems: KebabMenuItem[];
    onOpenContextMenu?: (e: React.MouseEvent) => void;
    /** Drop handler for in-panel moves + OS-file uploads into this
     * folder. Hover highlight is local state. */
    onDropInto?: (e: React.DragEvent) => void;
    /** In-panel drag source (move the whole folder). */
    draggable?: boolean;
    onDragStartRow?: (e: React.DragEvent) => void;
    onDragEndRow?: () => void;
    renaming?: boolean;
    onRenameCommit?: (newName: string) => void;
    onRenameCancel?: () => void;
    /** Keyboard-navigation identity + highlight. */
    rowKey?: string;
    focused?: boolean;
}

export const FolderRow: React.FC<FolderRowProps> = ({
    folder,
    depth,
    expanded,
    fileCount,
    isPending,
    loadedCount,
    onToggle,
    menuItems,
    onOpenContextMenu,
    onDropInto,
    draggable,
    onDragStartRow,
    onDragEndRow,
    renaming,
    onRenameCommit,
    onRenameCancel,
    rowKey,
    focused,
}) => {
    const indentPx = depth * 12;
    // dragenter/dragleave fire per child element; a counter survives
    // the churn where a plain boolean would flicker.
    const [dragHover, setDragHover] = useState(0);
    const acceptsDrop = (e: React.DragEvent) =>
        e.dataTransfer.types.includes(KEYS_MIME) ||
        e.dataTransfer.types.includes(FOLDER_MIME) ||
        e.dataTransfer.types.includes("Files");
    return (
        <li
            data-rowkey={rowKey}
            className={
                "flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer select-none " +
                "pointer-fine:hover:bg-surface-0 " +
                (dragHover > 0 ? "ring-1 ring-accent bg-accent-subtle " : "") +
                (focused && dragHover === 0 ? "ring-1 ring-accent " : "") +
                (isPending ? "opacity-80 " : "")
            }
            style={{paddingLeft: 8 + indentPx}}
            draggable={draggable || undefined}
            onDragStart={draggable && onDragStartRow ? onDragStartRow : undefined}
            onDragEnd={onDragEndRow}
            onClick={onToggle}
            onContextMenu={onOpenContextMenu}
            role="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} folder ${folder.name}`}
            onDragEnter={onDropInto ? (e) => {
                if (acceptsDrop(e)) setDragHover((c) => c + 1);
            } : undefined}
            onDragLeave={onDropInto ? () => setDragHover((c) => Math.max(0, c - 1)) : undefined}
            onDragOver={onDropInto ? (e) => {
                if (!acceptsDrop(e)) return;
                e.preventDefault();
                e.stopPropagation();
                e.dataTransfer.dropEffect = "move";
            } : undefined}
            onDrop={onDropInto ? (e) => {
                e.preventDefault();
                e.stopPropagation();
                setDragHover(0);
                onDropInto(e);
            } : undefined}
        >
            {/* Chevron — single right-pointing icon rotated 90° on
                expand. */}
            <ChevronRightIcon
                className={
                    "shrink-0 text-accent transition-transform duration-150 " +
                    (expanded ? "rotate-90" : "")
                }
            />
            {/* Folder glyph swaps closed↔open with the expand state.
                Same blue tone so eye + chevron read as one
                composite control. */}
            {expanded ? (
                <FolderOpenIcon className="shrink-0 text-accent"/>
            ) : (
                <FolderClosedIcon className="shrink-0 text-accent"/>
            )}
            {renaming && onRenameCommit && onRenameCancel ? (
                <InlineNameInput
                    initial={folder.name}
                    onCommit={onRenameCommit}
                    onCancel={onRenameCancel}
                />
            ) : (
                <span className="text-xs flex-1 min-w-0 truncate font-semibold">
                    {folder.name}/
                </span>
            )}
            {(loadedCount ?? 0) > 0 && (
                <span
                    className="shrink-0 inline-flex items-center gap-0.5 text-accent"
                    title={`${loadedCount} loaded model${loadedCount === 1 ? "" : "s"} inside`}
                >
                    <ViewIcon width="14px" height="14px"/>
                    {(loadedCount ?? 0) > 1 && (
                        <span className="text-[10px] tabular-nums">{loadedCount}</span>
                    )}
                </span>
            )}
            <span className="text-[10px] text-content-muted shrink-0">
                {isPending ? "empty" : fileCount}
            </span>
            {menuItems.length > 0 && (
                <span
                    className="shrink-0"
                    onClick={(e) => e.stopPropagation()}
                >
                    <RowKebabMenu
                        ariaLabel={`Organize folder ${folder.path}`}
                        buttonClassName="h-6 w-6 text-content pointer-fine:hover:bg-surface-2"
                        header={<span className="font-mono">{folder.path}/</span>}
                        items={menuItems}
                    />
                </span>
            )}
        </li>
    );
};

