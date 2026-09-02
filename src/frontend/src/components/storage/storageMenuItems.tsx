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

export interface FileMenuContext extends PlacementMenuContext {
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

/** Placement entries shared by file rows and procedural-model rows.
 *
 * Any loaded scene source can be moved — an uploaded GLB, a converted file, a
 * model's compiled result — so "put this one over there" must not have a
 * different answer depending on what produced the geometry. Both entries appear
 * only when the thing is actually IN the scene; offering them for something
 * unloaded would be a control with nothing to act on. */
export interface PlacementMenuContext {
    /** True when this row's geometry is currently loaded. */
    isLoaded?: boolean;
    onPlaceNextTo?: () => void;
    onTranslate?: () => void;
    onResetPlacement?: () => void;
    /** True when it has been moved off the shared origin. */
    isOffset?: boolean;
}

function pushPlacementItems(items: KebabMenuItem[], ctx: PlacementMenuContext): void {
    if (!ctx.isLoaded) return;
    if (ctx.onPlaceNextTo) {
        items.push({
            key: "place-next-to",
            label: "Place next to existing",
            title: "Move this beside whatever else is loaded, along +X, with a gap.",
            separatorBefore: true,
            onClick: ctx.onPlaceNextTo,
        });
    }
    if (ctx.onTranslate) {
        items.push({
            key: "translate",
            label: "Translate…",
            title: "Set this item's offset from the shared origin.",
            onClick: ctx.onTranslate,
        });
    }
    if (ctx.isOffset && ctx.onResetPlacement) {
        items.push({
            key: "reset-placement",
            label: "Reset placement",
            title: "Return this item to the shared origin.",
            onClick: ctx.onResetPlacement,
        });
    }
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
    pushPlacementItems(items, ctx);
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

export interface ProceduralMenuContext extends PlacementMenuContext {
    canMutate: boolean;
    onOpen: () => void;
    /** True when this model is the one the cellbuilder is editing. */
    isActive?: boolean;
    /** Make this the edited model. Exactly one can be active at a time. */
    onMakeActive?: () => void;
    /** Stop editing, leaving whatever is in the scene in the scene. */
    onDeactivate?: () => void;
    /** Load this model's last compiled result into the scene. Present only
     *  when there IS one; a model that has never compiled has nothing to show. */
    onViewResult?: () => void;
    onCopyPath?: () => void;
    onRename?: () => void;
    onMoveToFolder?: () => void;
    onDelete?: () => void;
}

/** Menu for a procedural model rendered as a leaf of the storage tree.
 *
 * Same kebab as files and folders, deliberately: a model is filed alongside
 * them and "how do I move this" should not have a different answer depending
 * on what the row happens to be backed by.
 *
 * What it does NOT offer is the file half — load into the scene, download,
 * convert. A model is a database row; those controls would promise operations
 * that cannot work on it. Rename and Move are the two that genuinely mean the
 * same thing here as for a file, and both are the SAME server call, because the
 * model's name is its path. */
export function buildProceduralMenuItems(
    displayName: string,
    ctx: ProceduralMenuContext,
): KebabMenuItem[] {
    const items: KebabMenuItem[] = [];
    items.push({
        key: "open",
        label: "Open in cellbuilder",
        onClick: ctx.onOpen,
    });
    // ACTIVE vs IN THE SCENE are two different things, and the menu keeps them
    // apart. Several models' compiled results can sit in the scene at once —
    // each loads under its own source name — but only one can be ACTIVE,
    // because the cellbuilder edits one document. "View compiled result" adds
    // to the scene; "Make active" changes what you are editing.
    if (ctx.isActive && ctx.onDeactivate) {
        items.push({
            key: "deactivate",
            label: "Stop editing (keep in scene)",
            title: "Closes the cellbuilder. Anything already loaded stays in the scene.",
            onClick: ctx.onDeactivate,
        });
    } else if (ctx.onMakeActive) {
        items.push({
            key: "make-active",
            label: "Make active",
            title: "Edit this model in the cellbuilder. Only one model is active at a time.",
            onClick: ctx.onMakeActive,
        });
    }
    if (ctx.onViewResult) {
        items.push({
            key: "view-result",
            label: "View compiled result",
            title: "Load this model's last compiled result into the scene, without making it active.",
            onClick: ctx.onViewResult,
        });
    }
    if (ctx.onCopyPath) {
        items.push({
            key: "copy-path",
            label: "Copy as path",
            title: "Copy this model's folder path to the clipboard.",
            onClick: ctx.onCopyPath,
        });
    }
    pushPlacementItems(items, ctx);
    if (ctx.canMutate && ctx.onRename) {
        items.push({
            key: "rename",
            label: "Rename…",
            title: `Rename "${displayName}" without moving it.`,
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
            title: "Deletes the procedural model and its compiled outputs.",
            onClick: ctx.onDelete,
        });
    }
    return items;
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
