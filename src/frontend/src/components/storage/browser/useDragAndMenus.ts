import React, {useState} from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import {runtime} from "@/runtime/config";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {canLoadIntoSceneLegacy, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {buildFileMenuItems, buildFolderMenuItems} from "../storageMenuItems";
import {FOLDER_MIME, KEYS_MIME, basenameOf, dirnameOf} from "./helpers";
import type {useProceduralModels} from "./useProceduralModels";

// In-panel drag & drop (file and folder moves, OS-file drops) and the menu
// item builders the kebab and the right-click context menu share.
export function useDragAndMenus(p: {
    selection: Set<string>;
    canMutate: boolean;
    loadedSourceNames: ReadonlySet<string>;
    viewingName: string | null;
    placementItems: ReturnType<typeof useProceduralModels>["placementItems"];
    runFolderMove: (folderPath: string, newPath: string) => Promise<void>;
    moveKeysWithProgress: (keys: string[], folder: string) => Promise<void>;
    uploadFilesTo: (list: File[], folder?: string) => Promise<void>;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    onLoadStreamer: (name: string) => void;
    onDownloadFile: (key: string) => void;
    setRenaming: (r: {kind: "file" | "folder"; path: string} | null) => void;
    onMoveSingleToFolder: (key: string) => void;
    onDeleteFile: (f: ServerFileEntry) => Promise<void>;
    uploadTargetRef: React.MutableRefObject<string | null>;
    fileInputRef: React.RefObject<HTMLInputElement | null>;
    setNewFolderAt: (at: string | null) => void;
    setExpandedFolders: React.Dispatch<React.SetStateAction<Set<string>>>;
    onMoveFolderInto: (folderPath: string) => void;
    onDeleteFolder: (path: string, fileCount: number, isPending: boolean) => Promise<void>;
}) {
    const {
        selection, canMutate, loadedSourceNames, viewingName, placementItems, runFolderMove,
        moveKeysWithProgress, uploadFilesTo, onToggle, onLoadStreamer, onDownloadFile, setRenaming,
        onMoveSingleToFolder, onDeleteFile, uploadTargetRef, fileInputRef, setNewFolderAt,
        setExpandedFolders, onMoveFolderInto, onDeleteFolder,
    } = p;
    // In-panel drag state: keys being dragged (for row dimming + the
    // move-to-root strip). Cleared on dragend/drop.
    const [draggingKeys, setDraggingKeys] = useState<string[] | null>(null);
    const [draggingFolder, setDraggingFolder] = useState<string | null>(null);

    // ── Drag & drop ─────────────────────────────────────────────────
    const onDragStartFile = (f: ServerFileEntry) => (e: React.DragEvent) => {
        // Dragging a selected row drags the whole selection; dragging
        // an unselected row drags just that file.
        const keys = selection.has(f.name) ? Array.from(selection) : [f.name];
        e.dataTransfer.setData(KEYS_MIME, JSON.stringify(keys));
        e.dataTransfer.effectAllowed = "move";
        setDraggingKeys(keys);
    };
    const onDragEndFile = () => setDraggingKeys(null);

    // Drop onto a folder path ("" = root). Internal drags move keys;
    // OS-file drops upload into the folder.
    const handleDropOnFolder = async (target: string, e: React.DragEvent) => {
        setDraggingKeys(null);
        setDraggingFolder(null);
        const folderPath = e.dataTransfer.getData(FOLDER_MIME);
        if (folderPath) {
            if (!canMutate) return;
            // No-ops: into itself, into its own subtree, or where it
            // already lives.
            if (target === folderPath || target.startsWith(folderPath + "/")) return;
            if (dirnameOf(folderPath) === target) return;
            const base = basenameOf(folderPath);
            await runFolderMove(folderPath, target ? `${target}/${base}` : base);
            return;
        }
        const txt = e.dataTransfer.getData(KEYS_MIME);
        if (txt) {
            if (!canMutate) return;
            let keys: string[] = [];
            try {
                keys = JSON.parse(txt);
            } catch {
                return;
            }
            keys = keys.filter((k) => typeof k === "string" && dirnameOf(k) !== target);
            await moveKeysWithProgress(keys, target);
            return;
        }
        if (e.dataTransfer.files?.length) {
            void uploadFilesTo(Array.from(e.dataTransfer.files), target || undefined);
        }
    };

    // ── Menu item builders (kebab + context menu share these) ───────
    const fileMenuItems = (f: ServerFileEntry, displayName: string): KebabMenuItem[] => {
        const busy = viewingName === f.name;
        return buildFileMenuItems(f, {
            ...placementItems(f.name, displayName),
            isLoaded: loadedSourceNames.has(f.name),
            busy,
            loadDisabled: !isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name),
            canMutate,
            onToggle: (next) => void onToggle(f, next),
            onLoadStreamer:
                runtime.isRestMode() && runtime.convertEnabled()
                    ? () => onLoadStreamer(f.name)
                    : undefined,
            onDownload: runtime.isRestMode() ? () => onDownloadFile(f.name) : undefined,
            onCopyPath: () => void writeToClipboard(f.name),
            onRename: () => setRenaming({kind: "file", path: f.name}),
            onMoveToFolder: () => onMoveSingleToFolder(f.name),
            onDelete: () => void onDeleteFile(f),
        });
    };
    // CI version blobs stay read-only: load/streamer/download only.
    const versionFileMenuItems = (f: ServerFileEntry): KebabMenuItem[] => {
        const busy = viewingName === f.name;
        return buildFileMenuItems(f, {
            isLoaded: loadedSourceNames.has(f.name),
            busy,
            loadDisabled: !isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name),
            canMutate: false,
            onToggle: (next) => void onToggle(f, next),
            onLoadStreamer:
                runtime.isRestMode() && runtime.convertEnabled()
                    ? () => onLoadStreamer(f.name)
                    : undefined,
            onDownload: runtime.isRestMode() ? () => onDownloadFile(f.name) : undefined,
            onCopyPath: () => void writeToClipboard(f.name),
        });
    };
    const folderMenuItems = (path: string, fileCount: number, isPending: boolean): KebabMenuItem[] =>
        buildFolderMenuItems(path, {
            canMutate,
            fileCount,
            onUploadHere: () => {
                uploadTargetRef.current = path;
                fileInputRef.current?.click();
            },
            onNewSubfolder: () => {
                setNewFolderAt(path);
                setExpandedFolders((prev) => new Set(prev).add(path));
            },
            onRename: () => setRenaming({kind: "folder", path}),
            onMoveInto: () => onMoveFolderInto(path),
            onDelete: () => void onDeleteFolder(path, fileCount, isPending),
        });

    const showRootDropStrip =
        (draggingKeys !== null && draggingKeys.some((k) => dirnameOf(k) !== "")) ||
        (draggingFolder !== null && dirnameOf(draggingFolder) !== "");

    return {
        draggingKeys,
        draggingFolder,
        setDraggingFolder,
        onDragStartFile,
        onDragEndFile,
        handleDropOnFolder,
        fileMenuItems,
        versionFileMenuItems,
        folderMenuItems,
        showRootDropStrip,
    };
}
