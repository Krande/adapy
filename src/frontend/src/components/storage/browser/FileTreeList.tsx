import React from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import type {ProceduralModelSummary} from "@/services/viewerApi";
import type {BuildSidecar} from "@/hooks/useBuildSidecars";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import InlineNameInput from "@/components/common/InlineNameInput";
import FolderClosedIcon from "../../icons/FolderClosedIcon";
import FileRow from "./FileRow";
import FolderRow from "./FolderRow";
import ProceduralModelRow from "./ProceduralModelRow";
import VersionsTree from "./VersionsTree";
import {BranchGroup, KEYS_MIME, FOLDER_MIME, ServerFileTreeNode, countFiles, dirnameOf} from "./helpers";
import type {CtxMenuState} from "./BrowserDialogs";

type CtxEvent = {clientX: number; clientY: number; preventDefault?: () => void; stopPropagation?: () => void};

// The scrolling file list: root drop strip, inline new-folder input, the
// regular-files tree (folders, files, procedural models) and the CI versions
// tree. Empty state is a drop zone.
export interface FileTreeListProps {
    files: ServerFileEntry[];
    regularFiles: ServerFileEntry[];
    versionBranches: BranchGroup[];
    visibleTree: ServerFileTreeNode[];
    pendingFolders: string[];
    sidecars: ReadonlyMap<string, BuildSidecar | null>;
    maximized: boolean;
    listScrollRef: React.RefObject<HTMLDivElement | null>;
    onListKeyDown: (e: React.KeyboardEvent) => void;
    handleDropOnFolder: (target: string, e: React.DragEvent) => Promise<void>;
    showRootDropStrip: boolean;
    newFolderAt: string | null;
    setNewFolderAt: (at: string | null) => void;
    onCreateFolder: (parent: string, rawName: string) => void;
    proceduralByName: Map<string, ProceduralModelSummary>;
    activeProcedural: string | null;
    openProceduralModel: (m: ProceduralModelSummary) => Promise<void>;
    isProceduralLoaded: (m: ProceduralModelSummary) => boolean;
    toggleProceduralLoaded: (m: ProceduralModelSummary, next: boolean) => Promise<void>;
    proceduralMenuItems: (m: ProceduralModelSummary, displayName: string) => KebabMenuItem[];
    fileMenuItems: (f: ServerFileEntry, displayName: string) => KebabMenuItem[];
    versionFileMenuItems: (f: ServerFileEntry) => KebabMenuItem[];
    folderMenuItems: (path: string, fileCount: number, isPending: boolean) => KebabMenuItem[];
    openCtxMenu: (e: CtxEvent, items: KebabMenuItem[], header?: React.ReactNode) => void;
    selection: Set<string>;
    onRowSelectToggle: (name: string, shiftKey?: boolean) => void;
    toggleSelection: (name: string) => void;
    focusedKey: string | null;
    setFocusedKey: (k: string | null) => void;
    viewingName: string | null;
    loadedSourceNames: ReadonlySet<string>;
    conversionJobs: Record<string, {progress: number; status?: string}>;
    expandedName: string | null;
    setExpandedName: (n: string | null) => void;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    setPickerName: (n: string | null) => void;
    queuedLoadNames: Set<string>;
    canMutate: boolean;
    onDragStartFile: (f: ServerFileEntry) => (e: React.DragEvent) => void;
    onDragEndFile: () => void;
    uploadFilesTo: (list: File[], folder?: string) => Promise<void>;
    draggingKeys: string[] | null;
    setDraggingFolder: (path: string | null) => void;
    renaming: {kind: "file" | "folder"; path: string} | null;
    setRenaming: (r: {kind: "file" | "folder"; path: string} | null) => void;
    onRenameFileCommit: (f: ServerFileEntry, rawName: string) => Promise<void>;
    onRenameFolderCommit: (folderPath: string, rawName: string, isPending: boolean) => void;
    expandedFolders: Set<string>;
    toggleFolder: (path: string) => void;
    setGitHistoryOpen: (open: boolean) => void;
}

const FileTreeList: React.FC<FileTreeListProps> = (props) => {
    const {
        files, regularFiles, versionBranches, visibleTree, pendingFolders, sidecars, maximized,
        listScrollRef, onListKeyDown, handleDropOnFolder, showRootDropStrip, newFolderAt,
        setNewFolderAt, onCreateFolder, proceduralByName, activeProcedural, openProceduralModel,
        isProceduralLoaded, toggleProceduralLoaded, proceduralMenuItems, fileMenuItems,
        versionFileMenuItems, folderMenuItems, openCtxMenu, selection, onRowSelectToggle,
        toggleSelection, focusedKey, setFocusedKey, viewingName, loadedSourceNames, conversionJobs,
        expandedName, setExpandedName, onToggle, setPickerName, queuedLoadNames, canMutate,
        onDragStartFile, onDragEndFile, uploadFilesTo, draggingKeys, setDraggingFolder, renaming,
        setRenaming, onRenameFileCommit, onRenameFolderCommit, expandedFolders, toggleFolder,
        setGitHistoryOpen,
    } = props;
    return (
        <>
            {files.length === 0 && pendingFolders.length === 0 && newFolderAt === null ? (
                <div
                    className="text-xs italic text-gray-300 rounded-sm border border-dashed border-gray-600 p-3"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                        e.preventDefault();
                        void handleDropOnFolder("", e);
                    }}
                >
                    No files yet. Use + to upload, or drop files here.
                </div>
            ) : (
                (() => {
                    const regular = regularFiles;
                    const branches = versionBranches;
                    return (
                        <div
                            ref={listScrollRef}
                            tabIndex={0}
                            onKeyDown={onListKeyDown}
                            className={
                                "flex flex-col overflow-auto focus:outline-hidden " +
                                "focus-visible:ring-1 focus-visible:ring-blue-500/40 rounded-sm " +
                                // Desktop compact keeps the fixed 20rem cap;
                                // maximized fills. On mobile compact the whole
                                // panel scrolls (root overflow-y-auto), so the
                                // list itself is uncapped there (no double scroll).
                                (maximized ? "flex-1 min-h-0" : "md:max-h-80")
                            }
                            // Background (non-row) drops land at root:
                            // internal drags move to root, OS files
                            // upload at top level. Rows stopPropagation
                            // when they handle a drop themselves.
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={(e) => {
                                e.preventDefault();
                                void handleDropOnFolder("", e);
                            }}
                        >
                            {showRootDropStrip && (
                                <div
                                    className={
                                        "mb-1 px-2 py-1 text-[11px] text-gray-300 rounded-sm " +
                                        "border border-dashed border-blue-500/60 bg-blue-900/20"
                                    }
                                    onDragOver={(e) => {
                                        e.preventDefault();
                                        e.dataTransfer.dropEffect = "move";
                                    }}
                                    onDrop={(e) => {
                                        e.preventDefault();
                                        e.stopPropagation();
                                        void handleDropOnFolder("", e);
                                    }}
                                >
                                    Drop here to move to root /
                                </div>
                            )}
                            {newFolderAt === "" && (
                                <div className="flex items-center gap-1.5 px-2 py-1">
                                    <FolderClosedIcon className="shrink-0 text-blue-400"/>
                                    <InlineNameInput
                                        initial=""
                                        placeholder="New folder name"
                                        onCommit={(v) => onCreateFolder("", v)}
                                        onCancel={() => setNewFolderAt(null)}
                                    />
                                </div>
                            )}
                            {(regular.length > 0 || pendingFolders.length > 0) && (() => {
                                const tree = visibleTree;
                                const renderNode = (
                                    node: ServerFileTreeNode,
                                    depth: number,
                                ): React.ReactNode => {
                                    if (node.kind === "file") {
                                        const model = proceduralByName.get(node.file.name);
                                        if (model) {
                                            return (
                                                <ProceduralModelRow
                                                    key={`procedural:${model.id}`}
                                                    model={model}
                                                    displayName={node.displayName}
                                                    indentLevel={depth}
                                                    active={activeProcedural === model.id}
                                                    onOpen={() => void openProceduralModel(model)}
                                                    isSelected={selection.has(model.name)}
                                                    onSelectToggle={onRowSelectToggle}
                                                    isLoaded={isProceduralLoaded(model)}
                                                    canLoad={!!model.latest_glb_key}
                                                    onToggleLoaded={(m, next) =>
                                                        void toggleProceduralLoaded(m, next)
                                                    }
                                                    rowKey={`file:${model.name}`}
                                                    focused={focusedKey === `file:${model.name}`}
                                                    menuItems={proceduralMenuItems(model, node.displayName)}
                                                    onOpenContextMenu={(e) =>
                                                        openCtxMenu(
                                                            e,
                                                            proceduralMenuItems(model, node.displayName),
                                                            <span className="font-mono" title={model.name}>
                                                                {model.name}
                                                            </span>,
                                                        )
                                                    }
                                                />
                                            );
                                        }
                                        const items = fileMenuItems(node.file, node.displayName);
                                        const fileDir = dirnameOf(node.file.name);
                                        return (
                                            <FileRow
                                                key={node.file.name}
                                                file={node.file}
                                                displayName={node.displayName}
                                                indentLevel={depth}
                                                viewingName={viewingName}
                                                loadedSourceNames={loadedSourceNames}
                                                conversionJobs={conversionJobs}
                                                expandedName={expandedName}
                                                setExpandedName={setExpandedName}
                                                onToggle={onToggle}
                                                setPickerName={setPickerName}
                                                isSelected={selection.has(node.file.name)}
                                                isQueued={queuedLoadNames.has(node.file.name)}
                                                onSelectToggle={onRowSelectToggle}
                                                rowKey={`file:${node.file.name}`}
                                                focused={focusedKey === `file:${node.file.name}`}
                                                menuItems={items}
                                                onOpenContextMenu={(e) =>
                                                    openCtxMenu(
                                                        e,
                                                        items,
                                                        <span className="font-mono" title={node.file.name}>
                                                            {node.file.name}
                                                        </span>,
                                                    )
                                                }
                                                draggable={canMutate}
                                                onDragStartRow={onDragStartFile(node.file)}
                                                onDragEndRow={onDragEndFile}
                                                onDropAt={(e) => {
                                                    // OS files dropped on a file row land in
                                                    // that row's folder; internal drags are a
                                                    // no-op here (folders are the targets).
                                                    if (e.dataTransfer.getData(KEYS_MIME)) return;
                                                    if (e.dataTransfer.files?.length) {
                                                        void uploadFilesTo(
                                                            Array.from(e.dataTransfer.files),
                                                            fileDir || undefined,
                                                        );
                                                    }
                                                }}
                                                dimmed={draggingKeys?.includes(node.file.name) ?? false}
                                                renaming={renaming?.kind === "file" && renaming.path === node.file.name}
                                                onRenameCommit={(v) => void onRenameFileCommit(node.file, v)}
                                                onRenameCancel={() => setRenaming(null)}
                                                showModified={maximized}
                                            />
                                        );
                                    }
                                    const expanded = expandedFolders.has(node.path);
                                    const total = countFiles(node);
                                    const isPending = total === 0;
                                    const items = folderMenuItems(node.path, total, isPending);
                                    const loadedCount = Array.from(loadedSourceNames)
                                        .filter((n) => n.startsWith(node.path + "/")).length;
                                    return (
                                        <React.Fragment key={`folder:${node.path}`}>
                                            <FolderRow
                                                folder={node}
                                                depth={depth}
                                                expanded={expanded}
                                                fileCount={total}
                                                isPending={isPending}
                                                loadedCount={loadedCount}
                                                onToggle={() => {
                                                    setFocusedKey(`folder:${node.path}`);
                                                    toggleFolder(node.path);
                                                }}
                                                rowKey={`folder:${node.path}`}
                                                focused={focusedKey === `folder:${node.path}`}
                                                menuItems={items}
                                                onOpenContextMenu={(e) =>
                                                    openCtxMenu(
                                                        e,
                                                        items,
                                                        <span className="font-mono" title={node.path}>
                                                            {node.path}/
                                                        </span>,
                                                    )
                                                }
                                                onDropInto={(e) => void handleDropOnFolder(node.path, e)}
                                                draggable={canMutate && !isPending}
                                                onDragStartRow={(e) => {
                                                    e.dataTransfer.setData(FOLDER_MIME, node.path);
                                                    e.dataTransfer.effectAllowed = "move";
                                                    setDraggingFolder(node.path);
                                                }}
                                                onDragEndRow={() => setDraggingFolder(null)}
                                                renaming={renaming?.kind === "folder" && renaming.path === node.path}
                                                onRenameCommit={(v) => onRenameFolderCommit(node.path, v, isPending)}
                                                onRenameCancel={() => setRenaming(null)}
                                            />
                                            {expanded && newFolderAt === node.path && (
                                                <li
                                                    className="flex items-center gap-1.5 px-2 py-1"
                                                    style={{paddingLeft: 8 + (depth + 1) * 12}}
                                                >
                                                    <FolderClosedIcon className="shrink-0 text-blue-400"/>
                                                    <InlineNameInput
                                                        initial=""
                                                        placeholder="New folder name"
                                                        onCommit={(v) => onCreateFolder(node.path, v)}
                                                        onCancel={() => setNewFolderAt(null)}
                                                    />
                                                </li>
                                            )}
                                            {expanded &&
                                                node.children.map((c) =>
                                                    renderNode(c, depth + 1),
                                                )}
                                        </React.Fragment>
                                    );
                                };
                                return (
                                    <ul className="flex flex-col divide-y divide-gray-700/60">
                                        {tree.map((n) => renderNode(n, 0))}
                                    </ul>
                                );
                            })()}
                            {branches.length > 0 && (
                                <VersionsTree
                                    branches={branches}
                                    sidecars={sidecars}
                                    viewingName={viewingName}
                                    loadedSourceNames={loadedSourceNames}
                                    conversionJobs={conversionJobs}
                                    expandedName={expandedName}
                                    setExpandedName={setExpandedName}
                                    onToggle={onToggle}
                                    setPickerName={setPickerName}
                                    onOpenGitHistory={() => setGitHistoryOpen(true)}
                                    selection={selection}
                                    onSelectToggle={toggleSelection}
                                    fileMenuItemsFor={versionFileMenuItems}
                                    onOpenContextMenu={openCtxMenu}
                                    showModified={maximized}
                                />
                            )}
                        </div>
                    );
                })()
            )}
        </>
    );
};

export default FileTreeList;
