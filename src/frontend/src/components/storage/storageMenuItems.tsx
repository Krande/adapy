// Single source of truth for the storage panel's file/folder action
// menus. Both entry points — the per-row kebab (touch/mobile) and the
// right-click context menu (desktop) — render the same items, so an
// action added here shows up in both without divergence.
//
// Mutating items (rename / move / delete / new subfolder) are included
// only when ``ctx.canMutate`` is set: personal scope for everyone,
// admin elsewhere (see useStorageMutations). VersionsTree rows pass
// ``canMutate: false`` regardless — CI version blobs stay read-only.

import {KebabMenuItem} from "@/components/common/PositionedMenu";
import {ServerFileEntry} from "@/state/serverInfoStore";

export interface FileMenuContext {
    isLoaded: boolean;
    /** This row (or another) is busy loading into the scene. */
    busy: boolean;
    /** No usable view target (legacy convert can't produce a GLB and
     * the format isn't a streaming source) — Load is disabled. */
    loadDisabled?: boolean;
    canMutate: boolean;
    onToggle: (nextChecked: boolean) => void;
    /** REST + convert mode only: streaming STEP load for big assemblies. */
    onLoadStreamer?: () => void;
    /** REST mode only. */
    onDownload?: () => void;
    /** Copy the file's storage path (the key shown in the menu header). */
    onCopyPath?: () => void;
    onRename?: () => void;
    onMoveToFolder?: () => void;
    onDelete?: () => void;
}

export function buildFileMenuItems(
    file: ServerFileEntry,
    ctx: FileMenuContext,
): KebabMenuItem[] {
    const items: KebabMenuItem[] = [];
    items.push({
        key: "toggle-load",
        label: ctx.isLoaded ? "Unload from scene" : "Load into scene",
        disabled: ctx.busy || (!ctx.isLoaded && ctx.loadDisabled),
        title: !ctx.isLoaded && ctx.loadDisabled
            ? "No viewable target for this format"
            : undefined,
        onClick: () => ctx.onToggle(!ctx.isLoaded),
    });
    if (ctx.onLoadStreamer && /\.(step|stp)$/i.test(file.name)) {
        items.push({
            key: "load-streamer",
            label: "Load using streamer",
            title: "Memory-bounded streaming STEP→GLB — for large assemblies that fail the normal load.",
            disabled: ctx.busy,
            onClick: ctx.onLoadStreamer,
        });
    }
    if (ctx.onDownload) {
        items.push({
            key: "download",
            label: "Download",
            onClick: ctx.onDownload,
        });
    }
    if (ctx.onCopyPath) {
        items.push({
            key: "copy-path",
            label: "Copy as path",
            title: "Copy this file's storage path to the clipboard.",
            onClick: ctx.onCopyPath,
        });
    }
    if (ctx.canMutate && ctx.onRename) {
        items.push({
            key: "rename",
            label: "Rename…",
            onClick: ctx.onRename,
        });
    }
    if (ctx.canMutate && ctx.onMoveToFolder) {
        items.push({
            key: "move-to-folder",
            label: "Move to folder…",
            onClick: ctx.onMoveToFolder,
        });
    }
    if (ctx.canMutate && ctx.onDelete) {
        items.push({
            key: "delete",
            label: "Delete",
            destructive: true,
            separatorBefore: true,
            title: "Deletes the file and its converted view caches.",
            onClick: ctx.onDelete,
        });
    }
    return items;
}

export interface FolderMenuContext {
    canMutate: boolean;
    fileCount: number;
    onRename?: () => void;
    onMoveInto?: () => void;
    onNewSubfolder?: () => void;
    onUploadHere?: () => void;
    onDelete?: () => void;
}

export function buildFolderMenuItems(
    folderPath: string,
    ctx: FolderMenuContext,
): KebabMenuItem[] {
    const items: KebabMenuItem[] = [];
    // Uploading is allowed in every scope the user can read, so
    // "Upload here…" stays available even when canMutate is off.
    if (ctx.onUploadHere) {
        items.push({
            key: "upload-here",
            label: "Upload here…",
            onClick: ctx.onUploadHere,
        });
    }
    if (!ctx.canMutate) return items;
    if (ctx.onNewSubfolder) {
        items.push({
            key: "new-subfolder",
            label: "New subfolder…",
            onClick: ctx.onNewSubfolder,
        });
    }
    if (ctx.onRename) {
        items.push({
            key: "rename",
            label: "Rename folder…",
            title: "Sibling-name rename. Subfolders preserved.",
            onClick: ctx.onRename,
        });
    }
    if (ctx.onMoveInto) {
        items.push({
            key: "move-into",
            label: "Move folder into…",
            title: "Move under a destination prefix. Subfolders preserved.",
            onClick: ctx.onMoveInto,
        });
    }
    if (ctx.onDelete) {
        items.push({
            key: "delete",
            label: `Delete folder (${ctx.fileCount} file${ctx.fileCount === 1 ? "" : "s"})`,
            destructive: true,
            separatorBefore: true,
            title: "Deletes every file under this folder, including their converted view caches.",
            onClick: ctx.onDelete,
        });
    }
    return items;
}
