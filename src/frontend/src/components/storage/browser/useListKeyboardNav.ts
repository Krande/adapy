import React, {useEffect, useRef, useState} from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import type {ServerFileTreeNode} from "./helpers";

export type FlatRow =
    | {kind: "folder"; path: string; depth: number; parent: string}
    | {kind: "file"; name: string; file: ServerFileEntry; depth: number; parent: string};

// Keyboard navigation over the visible (regular) tree: the flattened render
// order of the rows on screen (the versions subtree is excluded — its own
// collapsing structure), the focused row, and the arrow / Enter / Space /
// Delete handling.
export function useListKeyboardNav(p: {
    visibleTree: ServerFileTreeNode[];
    files: ServerFileEntry[];
    expandedFolders: Set<string>;
    toggleFolder: (path: string) => void;
    selection: Set<string>;
    setSelection: React.Dispatch<React.SetStateAction<Set<string>>>;
    toggleSelection: (name: string) => void;
    lastSelectedRef: React.MutableRefObject<string | null>;
    loadedSourceNames: ReadonlySet<string>;
    queuedLoadNames: Set<string>;
    canMutate: boolean;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    onDeleteSelected: () => Promise<void>;
    onDeleteFile: (f: ServerFileEntry) => Promise<void>;
    onDeleteFolder: (path: string, fileCount: number, isPending: boolean) => Promise<void>;
}) {
    const {
        visibleTree, files, expandedFolders, toggleFolder, selection, setSelection, toggleSelection,
        lastSelectedRef, loadedSourceNames, queuedLoadNames, canMutate, onToggle, onDeleteSelected,
        onDeleteFile, onDeleteFolder,
    } = p;
    // Keyboard-navigation focus, keyed `folder:<path>` / `file:<name>`.
    // Pointer interactions move it too, so arrows continue from the
    // last clicked row.
    const [focusedKey, setFocusedKey] = useState<string | null>(null);
    const listScrollRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!focusedKey) return;
        const el = listScrollRef.current?.querySelector(
            `[data-rowkey="${CSS.escape(focusedKey)}"]`,
        ) as HTMLElement | null;
        el?.scrollIntoView({block: "nearest"});
    }, [focusedKey]);

    const flatRows: FlatRow[] = [];
    {
        const walk = (nodes: ServerFileTreeNode[], depth: number, parent: string) => {
            for (const n of nodes) {
                if (n.kind === "folder") {
                    flatRows.push({kind: "folder", path: n.path, depth, parent});
                    if (expandedFolders.has(n.path)) walk(n.children, depth + 1, n.path);
                } else {
                    flatRows.push({kind: "file", name: n.file.name, file: n.file, depth, parent});
                }
            }
        };
        walk(visibleTree, 0, "");
    }
    const rowKeyOf = (r: FlatRow) => (r.kind === "folder" ? `folder:${r.path}` : `file:${r.name}`);

    const onListKeyDown = (e: React.KeyboardEvent) => {
        if (flatRows.length === 0) return;
        if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", " ", "Delete"].includes(e.key)) return;
        // Don't steal keys from the inline rename/new-folder inputs.
        if ((e.target as HTMLElement).tagName === "INPUT") return;
        e.preventDefault();
        e.stopPropagation();
        const idx = focusedKey ? flatRows.findIndex((r) => rowKeyOf(r) === focusedKey) : -1;
        const row = idx >= 0 ? flatRows[idx] : null;
        // Shift+Arrow extends the selection while moving focus —
        // multi-select without a pointer. Anchor the range on the row
        // we're leaving, then take the row we land on with us. Folder
        // rows just pass through (they can't be selected).
        const selectFileRow = (r: FlatRow | null) => {
            if (!r || r.kind !== "file") return;
            setSelection((prev) => {
                const next = new Set(prev);
                next.add(r.name);
                return next;
            });
            lastSelectedRef.current = r.name;
        };
        const focusAt = (i: number, extendSelection = false) => {
            const clamped = Math.max(0, Math.min(flatRows.length - 1, i));
            if (extendSelection) {
                selectFileRow(row);
                selectFileRow(flatRows[clamped]);
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
                if (!row) {
                    focusAt(0);
                } else if (row.kind === "folder") {
                    if (!expandedFolders.has(row.path)) toggleFolder(row.path);
                    else if (idx + 1 < flatRows.length && flatRows[idx + 1].parent === row.path) focusAt(idx + 1);
                }
                break;
            case "ArrowLeft":
                if (!row) {
                    focusAt(0);
                } else if (row.kind === "folder" && expandedFolders.has(row.path)) {
                    toggleFolder(row.path);
                } else if (row.parent) {
                    const pIdx = flatRows.findIndex((r) => r.kind === "folder" && r.path === row.parent);
                    if (pIdx >= 0) focusAt(pIdx);
                }
                break;
            case "Enter":
                if (!row) break;
                if (row.kind === "folder") toggleFolder(row.path);
                else void onToggle(row.file, !(loadedSourceNames.has(row.name) || queuedLoadNames.has(row.name)));
                break;
            case " ":
                if (row?.kind === "file") toggleSelection(row.name);
                break;
            case "Delete": {
                if (!canMutate) break;
                if (selection.size > 0) {
                    // The selection takes precedence over the focused row.
                    // Version blobs are server-protected — refuse loudly
                    // instead of half-deleting the batch.
                    const hasVersions = Array.from(selection).some((k) =>
                        k.replace(/^\/+/, "").startsWith("versions/"),
                    );
                    if (hasVersions) {
                        window.alert("CI version files can't be deleted");
                        break;
                    }
                    void onDeleteSelected();
                    break;
                }
                if (!row) break;
                if (row.kind === "file") {
                    void onDeleteFile(row.file);
                } else {
                    const prefix = row.path + "/";
                    const count = files.filter((x) =>
                        x.name.replace(/^\/+/, "").startsWith(prefix)).length;
                    void onDeleteFolder(row.path, count, count === 0);
                }
                break;
            }
        }
    };

    return {focusedKey, setFocusedKey, listScrollRef, flatRows, onListKeyDown};
}
