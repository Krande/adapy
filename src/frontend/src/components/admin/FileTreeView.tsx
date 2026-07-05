import React, {useEffect, useRef, useState} from "react";
import {
    FileTreeNode,
    loadExpandedFolders,
    saveExpandedFolders,
} from "@/utils/storage/fileTree";
import FileTypeIcon from "../icons/FileTypeIcon";
import FolderClosedIcon from "../icons/FolderClosedIcon";
import FolderOpenIcon from "../icons/FolderOpenIcon";
import ChevronRightIcon from "../icons/ChevronRightIcon";
import InlineNameInput from "@/components/common/InlineNameInput";
import {RowKebabMenu, KebabMenuItem} from "@/components/common/RowKebabMenu";

// Generic folder-tree view shared by the admin Corpus tab's file
// overview, its "Copy from scope" modal, and the utility file picker.
// Storage stays flat on the server; folders are presentational (a
// key's "/" segments). The tree shape itself is built by the caller
// via ``buildFileTree`` from ``@/utils/storage/fileTree`` — this
// component renders + manages collapse state, mirroring
// StorageBrowser's row look (indent per depth, chevron + folder
// glyph, file rows).
//
// Read-only by default. Passing ``mutations`` turns on the organize
// affordances from the storage panel: per-row kebab menus (rename /
// move / delete / new subfolder / upload here), inline rename inputs,
// and drag-and-drop moves between folders (plus a move-to-root strip).
// The component owns the interaction state; the server semantics —
// endpoints, confirm dialogs, list reloads — stay with the caller.

// Drag payload MIMEs. Deliberately distinct from StorageBrowser's
// ``application/x-adapy-keys`` (which carries a bare key array): the
// payload here embeds the source scope and drops verify it, so a drag
// that strays in from another panel (possibly another scope) is
// ignored instead of mis-moved. Types are readable during dragover,
// the payload only on drop — presence of the type alone distinguishes
// internal drags from OS-file drops.
const TREE_KEYS_MIME = "application/x-adapy-tree-keys";
const TREE_FOLDER_MIME = "application/x-adapy-tree-folder";

function dirnameOf(key: string): string {
    const i = key.lastIndexOf("/");
    return i >= 0 ? key.slice(0, i) : "";
}

function basenameOf(key: string): string {
    return key.split("/").pop() ?? key;
}

export interface FileTreeSelection {
    selected: ReadonlySet<string>;
    /** Toggle a batch of file keys on/off. A folder checkbox passes every
     * (enabled) descendant key — recursive select in one call. */
    onSelect: (keys: string[], select: boolean) => void;
}

/** Write affordances. All semantics (server calls, confirm dialogs,
 * list reloads, pending-folder bookkeeping) live in the caller — the
 * tree only runs the interaction (menus, inline inputs, drag & drop)
 * and validates names before dispatching. */
export interface FileTreeMutations {
    /** Inline rename committed: new basename for the file (no "/"). */
    renameFile: (key: string, newName: string) => void;
    /** Inline rename committed: new single-segment name for the folder. */
    renameFolder: (path: string, newName: string) => void;
    /** Drag-drop move of file keys into ``destFolder`` ("" = root). */
    moveKeys: (keys: string[], destFolder: string) => void;
    /** Drag-drop move of a whole folder under ``destFolder`` ("" = root). */
    moveFolder: (folderPath: string, destFolder: string) => void;
    deleteFile: (key: string) => void;
    /** ``fileCount`` = files under the prefix; 0 means a client-side
     * pending (empty) folder the caller can drop without a server call. */
    deleteFolder: (path: string, fileCount: number) => void;
    /** Bulk delete — the Delete key targets the whole selection when
     * one exists. The caller confirms with an overview of the keys. */
    deleteKeys?: (keys: string[]) => void;
    /** "New folder" input committed (single-segment name; ``parent``
     * "" = root). The caller materialises it (pending-folder state). */
    createFolder: (parent: string, name: string) => void;
    /** Open the caller's move-to-folder picker for one file. */
    requestMoveFile?: (key: string) => void;
    /** Open the caller's move-folder-into picker. */
    requestMoveFolder?: (path: string) => void;
    /** Upload OS files into a folder ("" = root) — used by the folder
     * menu's "Upload here…" and by OS-file drops. Omit to disable both. */
    uploadTo?: (folder: string, files: File[]) => void;
    downloadFile?: (key: string) => void;
}

interface FileTreeViewProps<T> {
    nodes: FileTreeNode<T>[];
    /** Stable identity for a file entry — the storage key. */
    getKey: (file: T) => string;
    /** Persist collapse state per-scope (same namespacing as StorageBrowser). */
    namespace: string;
    scope: string;
    /** Optional checkbox selection. A folder check toggles all descendant
     * file keys; partially-selected folders render an indeterminate box. */
    selection?: FileTreeSelection;
    /** Greyed, non-selectable files (e.g. already present in the corpus).
     * Excluded from folder-level select too. */
    isDisabled?: (file: T) => boolean;
    /** Right-aligned per-file slot for actions / labels (size, delete, …). */
    renderFileTail?: (file: T) => React.ReactNode;
    /** Enable the organize affordances (see FileTreeMutations). */
    mutations?: FileTreeMutations;
    /** Caller-specific kebab items appended between the standard file
     * actions and Delete (e.g. the corpus tab's "Copy to my files"). */
    extraFileMenuItems?: (key: string) => KebabMenuItem[];
    /** Where the "new folder" inline input shows: "" = top level, a
     * folder path = inside it, null/undefined = hidden. Owned by the
     * caller so a header-level button can trigger root creation; the
     * folder menu's "New subfolder…" routes through it too. */
    newFolderAt?: string | null;
    onNewFolderAtChange?: (v: string | null) => void;
}

// Every enabled descendant file key under a node — drives folder-level
// (recursive) selection and the all/some/none folder checkbox state.
function collectFileKeys<T>(
    node: FileTreeNode<T>,
    getKey: (file: T) => string,
    isDisabled?: (file: T) => boolean,
): string[] {
    if (node.kind === "file") {
        return isDisabled?.(node.file) ? [] : [getKey(node.file)];
    }
    const out: string[] = [];
    for (const child of node.children) {
        out.push(...collectFileKeys(child, getKey, isDisabled));
    }
    return out;
}

// Checkbox that can show the third (indeterminate) state — set
// imperatively since React doesn't expose ``indeterminate`` as a prop.
const TriCheckbox: React.FC<{
    checked: boolean;
    indeterminate?: boolean;
    disabled?: boolean;
    onChange: () => void;
}> = ({checked, indeterminate, disabled, onChange}) => {
    const ref = useRef<HTMLInputElement>(null);
    useEffect(() => {
        if (ref.current) ref.current.indeterminate = !!indeterminate && !checked;
    }, [indeterminate, checked]);
    return (
        <input
            ref={ref}
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={onChange}
            onClick={(e) => e.stopPropagation()}
        />
    );
};

export function FileTreeView<T>({
    nodes,
    getKey,
    namespace,
    scope,
    selection,
    isDisabled,
    renderFileTail,
    mutations,
    extraFileMenuItems,
    newFolderAt,
    onNewFolderAtChange,
}: FileTreeViewProps<T>): React.ReactElement {
    // Default fully collapsed; persisted per-scope so expand state survives
    // reloads but doesn't leak across scopes (matches StorageBrowser).
    const [expanded, setExpanded] = useState<Set<string>>(
        () => loadExpandedFolders(namespace, scope),
    );
    useEffect(() => {
        setExpanded(loadExpandedFolders(namespace, scope));
    }, [namespace, scope]);
    useEffect(() => {
        saveExpandedFolders(namespace, scope, expanded);
    }, [namespace, scope, expanded]);
    const toggleFolder = (path: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };
    // Optimistic expand-state re-key after a rename/move dispatch —
    // harmless if the server-side move fails (a stale entry just means
    // a collapsed folder).
    const rekeyExpanded = (oldPath: string, newPath: string) => {
        setExpanded((prev) => {
            const next = new Set<string>();
            for (const p of prev) {
                if (p === oldPath) next.add(newPath);
                else if (p.startsWith(oldPath + "/")) next.add(newPath + p.slice(oldPath.length));
                else next.add(p);
            }
            return next;
        });
    };

    // Inline rename target. One at a time, like StorageBrowser.
    const [renaming, setRenaming] = useState<{kind: "file" | "folder"; path: string} | null>(null);
    // In-flight drag payload (for row dimming + the move-to-root strip).
    const [dragKeys, setDragKeys] = useState<string[] | null>(null);
    const [dragFolder, setDragFolder] = useState<string | null>(null);
    // Folder path currently hovered by an accepted drag.
    const [dropTarget, setDropTarget] = useState<string | null>(null);
    // Keyboard-navigation focus, keyed `folder:<path>` / `file:<key>`.
    // Pointer interactions move it too, so arrows continue from the
    // last clicked row (mirrors StorageBrowser).
    const [focusedKey, setFocusedKey] = useState<string | null>(null);
    const wrapRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!focusedKey) return;
        const el = wrapRef.current?.querySelector(
            `[data-rowkey="${CSS.escape(focusedKey)}"]`,
        ) as HTMLElement | null;
        el?.scrollIntoView({block: "nearest"});
    }, [focusedKey]);

    // Hidden input backing the folder menu's "Upload here…". Clicked
    // synchronously inside the menu item's onClick so the file picker
    // keeps the user-activation gesture (iOS Safari refuses otherwise).
    const uploadInputRef = useRef<HTMLInputElement>(null);
    const uploadTargetRef = useRef<string>("");

    const acceptsDrop = (e: React.DragEvent) =>
        !!mutations &&
        (e.dataTransfer.types.includes(TREE_KEYS_MIME) ||
            e.dataTransfer.types.includes(TREE_FOLDER_MIME) ||
            (!!mutations.uploadTo && e.dataTransfer.types.includes("Files")));

    const onDragStartFile = (key: string) => (e: React.DragEvent) => {
        // Dragging a selected row drags the whole selection; dragging
        // an unselected row drags just that file (matches StorageBrowser).
        const keys = selection?.selected.has(key)
            ? Array.from(selection.selected)
            : [key];
        e.dataTransfer.setData(TREE_KEYS_MIME, JSON.stringify({scope, keys}));
        e.dataTransfer.effectAllowed = "move";
        setDragKeys(keys);
    };
    const onDragStartFolder = (path: string) => (e: React.DragEvent) => {
        e.dataTransfer.setData(TREE_FOLDER_MIME, JSON.stringify({scope, path}));
        e.dataTransfer.effectAllowed = "move";
        setDragFolder(path);
    };
    const clearDragState = () => {
        setDragKeys(null);
        setDragFolder(null);
        setDropTarget(null);
    };

    // Drop onto a folder path ("" = root). Internal drags move keys or
    // a whole folder subtree; OS-file drops upload into the folder.
    const handleDrop = (target: string, e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        clearDragState();
        if (!mutations) return;
        const rawFolder = e.dataTransfer.getData(TREE_FOLDER_MIME);
        if (rawFolder) {
            let p: {scope?: unknown; path?: unknown};
            try {
                p = JSON.parse(rawFolder);
            } catch {
                return;
            }
            if (p.scope !== scope || typeof p.path !== "string") return;
            const folderPath = p.path;
            // No-ops: into itself, into its own subtree, or where it
            // already lives.
            if (target === folderPath || target.startsWith(folderPath + "/")) return;
            if (dirnameOf(folderPath) === target) return;
            const base = basenameOf(folderPath);
            rekeyExpanded(folderPath, target ? `${target}/${base}` : base);
            mutations.moveFolder(folderPath, target);
            return;
        }
        const rawKeys = e.dataTransfer.getData(TREE_KEYS_MIME);
        if (rawKeys) {
            let p: {scope?: unknown; keys?: unknown};
            try {
                p = JSON.parse(rawKeys);
            } catch {
                return;
            }
            if (p.scope !== scope || !Array.isArray(p.keys)) return;
            const keys = p.keys.filter(
                (k): k is string => typeof k === "string" && dirnameOf(k) !== target,
            );
            if (keys.length > 0) mutations.moveKeys(keys, target);
            return;
        }
        if (e.dataTransfer.files?.length && mutations.uploadTo) {
            mutations.uploadTo(target, Array.from(e.dataTransfer.files));
        }
    };

    // ── Commit handlers (validate, then dispatch to the caller) ─────
    const onRenameFileCommit = (key: string, displayName: string, raw: string) => {
        setRenaming(null);
        if (!mutations) return;
        const name = raw.trim();
        if (!name || name === displayName) return;
        if (name.includes("/")) {
            window.alert("Name must not contain '/' — use Move to folder… instead");
            return;
        }
        mutations.renameFile(key, name);
    };
    const onRenameFolderCommit = (path: string, raw: string) => {
        setRenaming(null);
        if (!mutations) return;
        const name = raw.trim().replace(/^\/+|\/+$/g, "");
        if (!name || name === basenameOf(path)) return;
        if (name.includes("/")) {
            window.alert("Rename must be a single name; use Move folder into… for nested moves");
            return;
        }
        const parent = dirnameOf(path);
        rekeyExpanded(path, parent ? `${parent}/${name}` : name);
        mutations.renameFolder(path, name);
    };
    const onCreateFolderCommit = (parent: string, raw: string) => {
        onNewFolderAtChange?.(null);
        if (!mutations) return;
        const name = raw.trim().replace(/^\/+|\/+$/g, "");
        if (!name) return;
        if (name.includes("/")) {
            window.alert("Folder name must not contain '/'");
            return;
        }
        setExpanded((prev) => {
            const next = new Set(prev);
            if (parent) next.add(parent);
            next.add(parent ? `${parent}/${name}` : name);
            return next;
        });
        mutations.createFolder(parent, name);
    };

    // ── Kebab menu builders ──────────────────────────────────────────
    const fileMenuItems = (key: string): KebabMenuItem[] => {
        if (!mutations) return [];
        const items: KebabMenuItem[] = [];
        if (mutations.downloadFile) {
            items.push({
                key: "download",
                label: "Download",
                onClick: () => mutations.downloadFile!(key),
            });
        }
        items.push({
            key: "rename",
            label: "Rename…",
            onClick: () => setRenaming({kind: "file", path: key}),
        });
        if (mutations.requestMoveFile) {
            items.push({
                key: "move-to-folder",
                label: "Move to folder…",
                onClick: () => mutations.requestMoveFile!(key),
            });
        }
        items.push(...(extraFileMenuItems?.(key) ?? []));
        items.push({
            key: "delete",
            label: "Delete",
            destructive: true,
            separatorBefore: true,
            onClick: () => mutations.deleteFile(key),
        });
        return items;
    };
    const folderMenuItems = (path: string, fileCount: number): KebabMenuItem[] => {
        if (!mutations) return [];
        const items: KebabMenuItem[] = [];
        if (mutations.uploadTo) {
            items.push({
                key: "upload-here",
                label: "Upload here…",
                onClick: () => {
                    uploadTargetRef.current = path;
                    uploadInputRef.current?.click();
                },
            });
        }
        items.push({
            key: "new-subfolder",
            label: "New subfolder…",
            onClick: () => {
                setExpanded((prev) => new Set(prev).add(path));
                onNewFolderAtChange?.(path);
            },
        });
        items.push({
            key: "rename",
            label: "Rename folder…",
            title: "Sibling-name rename. Subfolders preserved.",
            onClick: () => setRenaming({kind: "folder", path}),
        });
        if (mutations.requestMoveFolder) {
            items.push({
                key: "move-into",
                label: "Move folder into…",
                title: "Move under a destination prefix. Subfolders preserved.",
                onClick: () => mutations.requestMoveFolder!(path),
            });
        }
        items.push({
            key: "delete",
            label: `Delete folder (${fileCount} file${fileCount === 1 ? "" : "s"})`,
            destructive: true,
            separatorBefore: true,
            onClick: () => mutations.deleteFolder(path, fileCount),
        });
        return items;
    };

    const newFolderInputRow = (parent: string, depth: number) => (
        <li
            className="flex items-center gap-1.5 px-2 py-1"
            style={{paddingLeft: 8 + depth * 12}}
        >
            <FolderClosedIcon className="shrink-0 text-blue-400"/>
            <InlineNameInput
                initial=""
                placeholder="New folder name"
                onCommit={(v) => onCreateFolderCommit(parent, v)}
                onCancel={() => onNewFolderAtChange?.(null)}
            />
        </li>
    );

    const renderNode = (node: FileTreeNode<T>, depth: number): React.ReactNode => {
        const indentPx = depth * 12;
        if (node.kind === "file") {
            const key = getKey(node.file);
            const disabled = isDisabled?.(node.file) ?? false;
            const checked = selection ? selection.selected.has(key) : false;
            const onRowSelect = () => {
                if (!selection || disabled) return;
                selection.onSelect([key], !checked);
            };
            const isRenaming = renaming?.kind === "file" && renaming.path === key;
            const menuItems = fileMenuItems(key);
            const rowKey = `file:${key}`;
            return (
                <li
                    key={rowKey}
                    data-rowkey={rowKey}
                    className={
                        "flex items-center gap-1.5 px-2 py-1 rounded select-none " +
                        (dragKeys?.includes(key) ? "opacity-40 " : "") +
                        (focusedKey === rowKey ? "ring-1 ring-blue-400/70 " : "") +
                        (checked ? "bg-amber-700/30 " : "") +
                        (disabled
                            ? "opacity-50 "
                            : selection
                                ? "cursor-pointer hover:bg-gray-800/60 "
                                : "hover:bg-gray-800/60 ")
                    }
                    style={{paddingLeft: 8 + indentPx}}
                    onClick={() => {
                        setFocusedKey(rowKey);
                        onRowSelect();
                    }}
                    draggable={(!!mutations && !isRenaming) || undefined}
                    onDragStart={mutations ? onDragStartFile(key) : undefined}
                    onDragEnd={mutations ? clearDragState : undefined}
                    // Internal drops on a file row are a no-op (folders are
                    // the targets) — swallowed so they don't bubble to the
                    // background handler and move to root. OS-file drops
                    // land in the row's folder.
                    onDragOver={mutations ? (e) => {
                        if (!acceptsDrop(e)) return;
                        e.preventDefault();
                        e.stopPropagation();
                    } : undefined}
                    onDrop={mutations ? (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        clearDragState();
                        if (
                            e.dataTransfer.getData(TREE_KEYS_MIME) ||
                            e.dataTransfer.getData(TREE_FOLDER_MIME)
                        ) return;
                        if (e.dataTransfer.files?.length && mutations.uploadTo) {
                            mutations.uploadTo(dirnameOf(key), Array.from(e.dataTransfer.files));
                        }
                    } : undefined}
                >
                    {selection && (
                        <input
                            type="checkbox"
                            className="shrink-0 cursor-pointer disabled:cursor-not-allowed"
                            checked={checked}
                            disabled={disabled}
                            onChange={onRowSelect}
                            onClick={(e) => e.stopPropagation()}
                        />
                    )}
                    <FileTypeIcon name={key}/>
                    {isRenaming ? (
                        <InlineNameInput
                            initial={node.displayName}
                            selectStem
                            onCommit={(v) => onRenameFileCommit(key, node.displayName, v)}
                            onCancel={() => setRenaming(null)}
                        />
                    ) : (
                        <span
                            className="font-mono text-xs text-gray-200 flex-1 min-w-0 truncate"
                            title={key}
                        >
                            {node.displayName}
                        </span>
                    )}
                    {renderFileTail && (
                        <span className="shrink-0" onClick={(e) => e.stopPropagation()}>
                            {renderFileTail(node.file)}
                        </span>
                    )}
                    {menuItems.length > 0 && (
                        <span className="shrink-0" onClick={(e) => e.stopPropagation()}>
                            <RowKebabMenu
                                ariaLabel={`Actions for ${node.displayName}`}
                                buttonClassName="h-6 w-6 text-gray-300 hover:bg-gray-700"
                                header={<span className="font-mono" title={key}>{key}</span>}
                                items={menuItems}
                            />
                        </span>
                    )}
                </li>
            );
        }
        const isOpen = expanded.has(node.path);
        const fileKeys = collectFileKeys(node, getKey, isDisabled);
        const total = fileKeys.length;
        const selCount = selection
            ? fileKeys.reduce((n, k) => (selection.selected.has(k) ? n + 1 : n), 0)
            : 0;
        const allSelected = total > 0 && selCount === total;
        const onFolderSelect = () => {
            if (!selection || total === 0) return;
            selection.onSelect(fileKeys, !allSelected);
        };
        const isRenaming = renaming?.kind === "folder" && renaming.path === node.path;
        const menuItems = folderMenuItems(node.path, total);
        const isDropTarget = dropTarget === node.path;
        const rowKey = `folder:${node.path}`;
        return (
            <React.Fragment key={rowKey}>
                <li
                    data-rowkey={rowKey}
                    className={
                        "flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer " +
                        "select-none hover:bg-gray-800/80 " +
                        (dragFolder === node.path ? "opacity-40 " : "") +
                        (focusedKey === rowKey && !isDropTarget ? "ring-1 ring-blue-400/70 " : "") +
                        (isDropTarget ? "ring-1 ring-blue-400 bg-blue-900/30 " : "")
                    }
                    style={{paddingLeft: 8 + indentPx}}
                    onClick={() => {
                        setFocusedKey(rowKey);
                        toggleFolder(node.path);
                    }}
                    role="button"
                    aria-expanded={isOpen}
                    aria-label={`${isOpen ? "Collapse" : "Expand"} folder ${node.name}`}
                    draggable={(!!mutations && !isRenaming) || undefined}
                    onDragStart={mutations ? onDragStartFolder(node.path) : undefined}
                    onDragEnd={mutations ? clearDragState : undefined}
                    onDragOver={mutations ? (e) => {
                        if (!acceptsDrop(e)) return;
                        e.preventDefault();
                        e.stopPropagation();
                        e.dataTransfer.dropEffect = "move";
                        setDropTarget(node.path);
                    } : undefined}
                    onDragLeave={mutations ? () => {
                        setDropTarget((prev) => (prev === node.path ? null : prev));
                    } : undefined}
                    onDrop={mutations ? (e) => handleDrop(node.path, e) : undefined}
                >
                    {selection && (
                        <TriCheckbox
                            checked={allSelected}
                            indeterminate={selCount > 0}
                            disabled={total === 0}
                            onChange={onFolderSelect}
                        />
                    )}
                    <ChevronRightIcon
                        className={
                            "shrink-0 text-blue-400 transition-transform duration-150 " +
                            (isOpen ? "rotate-90" : "")
                        }
                    />
                    {isOpen ? (
                        <FolderOpenIcon className="shrink-0 text-blue-400"/>
                    ) : (
                        <FolderClosedIcon className="shrink-0 text-blue-400"/>
                    )}
                    {isRenaming ? (
                        <InlineNameInput
                            initial={node.name}
                            onCommit={(v) => onRenameFolderCommit(node.path, v)}
                            onCancel={() => setRenaming(null)}
                        />
                    ) : (
                        <span className="text-xs flex-1 min-w-0 truncate font-semibold text-gray-200">
                            {node.name}/
                        </span>
                    )}
                    <span className="text-[10px] text-gray-400 shrink-0">
                        {total === 0 ? "empty" : total}
                    </span>
                    {menuItems.length > 0 && (
                        <span className="shrink-0" onClick={(e) => e.stopPropagation()}>
                            <RowKebabMenu
                                ariaLabel={`Organize folder ${node.path}`}
                                buttonClassName="h-6 w-6 text-gray-300 hover:bg-gray-700"
                                header={<span className="font-mono">{node.path}/</span>}
                                items={menuItems}
                            />
                        </span>
                    )}
                </li>
                {isOpen && newFolderAt === node.path && newFolderInputRow(node.path, depth + 1)}
                {isOpen && node.children.map((c) => renderNode(c, depth + 1))}
            </React.Fragment>
        );
    };

    const showRootDropStrip =
        (dragKeys !== null && dragKeys.some((k) => dirnameOf(k) !== "")) ||
        (dragFolder !== null && dirnameOf(dragFolder) !== "");

    // ── Keyboard navigation over the visible rows ────────────────────
    // Flattened render order of what's currently on screen. Shift+
    // Arrow extends the selection while moving focus (multi-select
    // without a pointer); plain arrows just move focus. Space toggles
    // the focused file; Enter toggles a folder; Delete removes the
    // selection (or the focused row when nothing is selected).
    type FlatRow =
        | {kind: "folder"; path: string; count: number}
        | {kind: "file"; key: string; disabled: boolean};
    const flatRows: FlatRow[] = [];
    {
        const walk = (ns: FileTreeNode<T>[]) => {
            for (const n of ns) {
                if (n.kind === "folder") {
                    flatRows.push({
                        kind: "folder",
                        path: n.path,
                        count: collectFileKeys(n, getKey).length,
                    });
                    if (expanded.has(n.path)) walk(n.children);
                } else {
                    const k = getKey(n.file);
                    flatRows.push({
                        kind: "file",
                        key: k,
                        disabled: isDisabled?.(n.file) ?? false,
                    });
                }
            }
        };
        walk(nodes);
    }
    const rowKeyOf = (r: FlatRow) => (r.kind === "folder" ? `folder:${r.path}` : `file:${r.key}`);

    const onListKeyDown = (e: React.KeyboardEvent) => {
        if (flatRows.length === 0) return;
        if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", " ", "Delete"].includes(e.key)) return;
        // Don't steal keys from the inline rename/new-folder inputs.
        if ((e.target as HTMLElement).tagName === "INPUT") return;
        e.preventDefault();
        e.stopPropagation();
        const idx = focusedKey ? flatRows.findIndex((r) => rowKeyOf(r) === focusedKey) : -1;
        const row = idx >= 0 ? flatRows[idx] : null;
        const selectRow = (r: FlatRow | null) => {
            if (!selection || !r || r.kind !== "file" || r.disabled) return;
            selection.onSelect([r.key], true);
        };
        const focusAt = (i: number, extendSelection: boolean) => {
            const clamped = Math.max(0, Math.min(flatRows.length - 1, i));
            if (extendSelection) {
                // Anchor the range on the row we're leaving, then take
                // the row we land on with us.
                selectRow(row);
                selectRow(flatRows[clamped]);
            }
            setFocusedKey(rowKeyOf(flatRows[clamped]));
        };
        switch (e.key) {
            case "ArrowDown":
                focusAt(idx < 0 ? 0 : idx + 1, e.shiftKey);
                break;
            case "ArrowUp":
                focusAt(idx < 0 ? flatRows.length - 1 : idx - 1, e.shiftKey);
                break;
            case "ArrowRight":
                if (row?.kind === "folder" && !expanded.has(row.path)) toggleFolder(row.path);
                break;
            case "ArrowLeft":
                if (row?.kind === "folder" && expanded.has(row.path)) toggleFolder(row.path);
                break;
            case "Enter":
                if (row?.kind === "folder") toggleFolder(row.path);
                break;
            case " ":
                if (row?.kind === "file" && selection && !row.disabled) {
                    selection.onSelect([row.key], !selection.selected.has(row.key));
                }
                break;
            case "Delete":
                if (!mutations) break;
                if (selection && selection.selected.size > 0) {
                    // A selection takes precedence over the focused row —
                    // no deleteKeys handler means no keyboard bulk delete
                    // (never silently fall back to the focused file).
                    mutations.deleteKeys?.(Array.from(selection.selected));
                    break;
                }
                if (row?.kind === "file" && !row.disabled) {
                    mutations.deleteFile(row.key);
                } else if (row?.kind === "folder") {
                    mutations.deleteFolder(row.path, row.count);
                }
                break;
        }
    };

    return (
        <div
            ref={wrapRef}
            tabIndex={0}
            onKeyDown={onListKeyDown}
            className="focus:outline-hidden focus-visible:ring-1 focus-visible:ring-blue-500/40 rounded-sm"
            // Background (non-row) drops land at root: internal drags
            // move to root, OS files upload at top level. Folder rows
            // stopPropagation when they handle a drop themselves.
            onDragOver={mutations ? (e) => {
                if (acceptsDrop(e)) e.preventDefault();
            } : undefined}
            onDrop={mutations ? (e) => handleDrop("", e) : undefined}
        >
            {mutations?.uploadTo && (
                <input
                    ref={uploadInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                        const picked = Array.from(e.target.files ?? []);
                        e.target.value = "";
                        if (picked.length > 0) {
                            mutations.uploadTo!(uploadTargetRef.current, picked);
                        }
                    }}
                />
            )}
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
                    onDrop={(e) => handleDrop("", e)}
                >
                    Drop here to move to root /
                </div>
            )}
            <ul className="flex flex-col divide-y divide-gray-700/60 text-xs">
                {newFolderAt === "" && newFolderInputRow("", 0)}
                {nodes.map((n) => renderNode(n, 0))}
            </ul>
        </div>
    );
}

export default FileTreeView;
