import React from "react";
import type {AdminFileEntry} from "@/services/viewerApi";
import type {FolderNode} from "@/utils/storage/fileTree";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";

interface FolderRowProps {
    folder: FolderNode<AdminFileEntry>;
    depth: number;
    fileCount: number;
    expanded: boolean;
    onToggle: () => void;
    onRename: () => void;
    onMoveInto: () => void;
    busyMoving: boolean;
}

function folderMenuItems(
    folderPath: string,
    onRename: () => void,
    onMoveInto: () => void,
    busy: boolean,
) {
    return [
        {
            key: "rename",
            label: busy ? "Renaming…" : "Rename folder…",
            disabled: busy,
            title: `Rename "${folderPath}" (sibling-name; no slashes). Subfolders preserved.`,
            onClick: onRename,
        },
        {
            key: "move-into",
            label: busy ? "Moving…" : "Move folder into…",
            disabled: busy,
            title: `Move "${folderPath}" under a destination prefix. Subfolders preserved.`,
            onClick: onMoveInto,
        },
    ];
}

// Desktop folder row — spans the entire table width so the folder
// name + file count read like a section header inside the existing
// admin grid. Click anywhere on the row to toggle. Kebab on the right
// houses rename / move-into; portal-anchored so the menu doesn't
// clip against the table's scroll container.
const FolderTableRow: React.FC<FolderRowProps> = ({
    folder, depth, fileCount, expanded, onToggle,
    onRename, onMoveInto, busyMoving,
}) => (
    <tr className="border-t border-gray-800 bg-gray-900/40 hover:bg-gray-800">
        <td colSpan={7} className="px-3 py-1.5">
            <div className="flex items-center gap-1">
                <button
                    type="button"
                    onClick={onToggle}
                    className="flex items-center gap-1 text-left text-gray-200 flex-1 min-w-0"
                    style={{paddingLeft: `${depth * 1.25}rem`}}
                    aria-expanded={expanded}
                >
                    <span className="inline-block w-3 text-gray-400 text-xs">
                        {expanded ? "▾" : "▸"}
                    </span>
                    <span className="font-medium">{folder.name}</span>
                    <span className="ml-2 text-[11px] text-gray-500">
                        ({fileCount} file{fileCount === 1 ? "" : "s"})
                    </span>
                </button>
                <RowKebabMenu
                    ariaLabel={`Organize folder ${folder.path}`}
                    disabled={busyMoving}
                    items={folderMenuItems(folder.path, onRename, onMoveInto, busyMoving)}
                />
            </div>
        </td>
    </tr>
);

// Mobile folder row — same affordance in <li> form so the SourceCard
// list stays a single <ul>. Tap target sized for thumbs.
const FolderCardRow: React.FC<FolderRowProps> = ({
    folder, depth, fileCount, expanded, onToggle,
    onRename, onMoveInto, busyMoving,
}) => (
    <li className="bg-gray-900/40 hover:bg-gray-800 px-3 py-2 flex items-center gap-1"
        style={{paddingLeft: `${0.75 + depth * 1.0}rem`}}>
        <button
            type="button"
            onClick={onToggle}
            className="flex-1 min-w-0 text-left flex items-center gap-1"
            aria-expanded={expanded}
        >
            <span className="inline-block w-3 text-gray-400 text-xs">
                {expanded ? "▾" : "▸"}
            </span>
            <span className="font-medium text-gray-100 text-sm">{folder.name}</span>
            <span className="ml-auto text-[11px] text-gray-500">
                {fileCount} file{fileCount === 1 ? "" : "s"}
            </span>
        </button>
        <RowKebabMenu
            ariaLabel={`Organize folder ${folder.path}`}
            disabled={busyMoving}
            buttonClassName="h-9 w-9"
            items={folderMenuItems(folder.path, onRename, onMoveInto, busyMoving)}
        />
    </li>
);

export {FolderTableRow, FolderCardRow};
