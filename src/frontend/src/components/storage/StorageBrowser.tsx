import {PanelCard} from "@/components/ui/PanelFrame";
import React, {useEffect, useMemo, useRef, useState} from "react";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {useConversionStore} from "@/state/conversionStore";
import {useModelState} from "@/state/modelState";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useLoadQueueStore} from "@/state/loadQueueStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {buildFileTree, collectFolderPaths, partitionUiHidden} from "@/utils/storage/fileTree";
import {useBuildSidecars} from "@/hooks/useBuildSidecars";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import {alertDialog, confirmDialog, errorText} from "@/plugins/dialogs";
import {useStorageMutations} from "./useStorageMutations";
import BrowserHeader from "./browser/BrowserHeader";
import SelectionToolbar from "./browser/SelectionToolbar";
import UploadProgress from "./browser/UploadProgress";
import UnsavedModelRow, {unsavedModelName} from "./browser/UnsavedModelRow";
import FileTreeList from "./browser/FileTreeList";
import BrowserDialogs, {CtxMenuState} from "./browser/BrowserDialogs";
import {classifyFiles} from "./browser/helpers";
import {useFolderTree} from "./browser/useFolderTree";
import {FolderPicker, useUploads} from "./browser/useUploads";
import {useProceduralModels} from "./browser/useProceduralModels";
import {useFileOps} from "./browser/useFileOps";
import {BulkBusy, useBulkActions} from "./browser/useBulkActions";
import {useDragAndMenus} from "./browser/useDragAndMenus";
import {useListKeyboardNav} from "./browser/useListKeyboardNav";
import {useRowLoads} from "./browser/useRowLoads";
import {selectionMenuItems, type SelectionMenuContext} from "./storageMenuItems";
import type {ProceduralModelSummary} from "@/services/viewerApi";

// The storage panel: composition of the hooks and pieces under ./browser.
// State that several pieces share (selection, load queue, menus) lives in
// `StorageBody`; everything else is owned by the hook that acts on it.

/** Below this width of the panel, `showModified: "auto"` hides the Modified column. */
export const STORAGE_MODIFIED_MIN_PX = 420;

export interface StorageBodyProps {
    /** @deprecated Ignored: the panel no longer maximizes. */
    maximized?: boolean;
    /** @deprecated Ignored: the panel no longer maximizes. */
    setMaximized?: React.Dispatch<React.SetStateAction<boolean>>;
    /** Tell each row's scene load on the row (percent, stage, error, Cancel and Dismiss,
     *  also in its menu) instead of a spinner; the rows claim those loads, so the progress
     *  toast leaves them out. Default off. */
    rowLoadProgress?: boolean;
    /** Where a selection's bulk actions are: the toolbar under the header (default), or
     *  an "N selected" group in each selected row's menu. */
    selectionActions?: "toolbar" | "rowMenu";
    /** Offer "Convert…" in a file row's menu, which calls this with the file's key. */
    onConvert?: (key: string) => void;
    /** Show a Close button in the header, which calls this. */
    onClose?: () => void;
    /** The header's scope line becomes a scope picker; switching asks first when it
     *  would unload models. Default off. */
    scopePicker?: boolean;
    /** The Modified column: shown (default), hidden, or "auto": hidden while the panel is
     *  narrower than `STORAGE_MODIFIED_MIN_PX`. */
    showModified?: boolean | "auto";
    /** The file list takes the height its host gives the panel (a dock or a window)
     *  instead of the column's fixed cap. Default off. */
    fillHeight?: boolean;
}

/** The storage browser's content: header, selection toolbar, upload progress, the file tree and its dialogs. */
export const StorageBody: React.FC<StorageBodyProps> = ({
    rowLoadProgress = false,
    selectionActions = "toolbar",
    onConvert,
    onClose,
    scopePicker = false,
    showModified = true,
    fillHeight = false,
}) => {
    const allFiles = useServerInfoStore((s) => s.serverFileObjects);
    // Published-asset blobs are storage, not "my files": a publishing scope
    // holds thousands under assets/<collection>/<subject>/<revision>/, and none
    // was uploaded by anyone here — they bury the handful of sources this
    // browser exists to show. Hidden in the VIEW only; the API still indexes
    // them, because plugins project their published hierarchy out of exactly
    // that listing. The count is surfaced below rather than swallowed.
    const {visible: files, hidden: hiddenFiles} = useMemo(
        () => partitionUiHidden(allFiles, (f) => f.name),
        [allFiles],
    );
    const {sidecars} = useBuildSidecars(files);
    const conversionJobs = useConversionStore((s) => s.jobs);
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);
    const anyLoaded = loadedSourceNames.size > 0;
    const currentScope = useScopeStore((s) => s.current);
    // Active "Show all" run — disables the per-row toggles while we're
    // overlaying every file in sequence, so the user can't kick off a
    // second batch on top of the first.
    const [bulkBusy, setBulkBusy] = useState<BulkBusy>(null);
    const [gitHistoryOpen, setGitHistoryOpen] = useState(false);
    // Selection: a Set of file names driving the bulk-action toolbar
    // under the header (load / unload / move / delete). The per-row
    // checkbox toggles membership — loading into the scene is an
    // explicit action (toolbar or row menu), never a checkbox side
    // effect. Long-press still selects (mobile ergonomics).
    const [selection, setSelection] = useState<Set<string>>(() => new Set());
    const inSelectionMode = selection.size > 0;
    const toggleSelection = (name: string) => {
        setSelection((prev) => {
            const next = new Set(prev);
            if (next.has(name)) next.delete(name);
            else next.add(name);
            return next;
        });
    };
    const clearSelection = () => setSelection(new Set());
    // Anchor for shift-click range selection (the last row toggled).
    const lastSelectedRef = useRef<string | null>(null);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    // Scene loads run through the sequential load queue; the row
    // spinner tracks whichever model the queue is currently loading.
    const loadCurrent = useLoadQueueStore((s) => s.current);
    const loadQueued = useLoadQueueStore((s) => s.queued);
    const queueEnqueue = useLoadQueueStore((s) => s.enqueue);
    const removeQueuedLoad = useLoadQueueStore((s) => s.removeQueued);
    const viewingName = loadCurrent?.name ?? null;
    const queuedLoadNames = new Set(loadQueued.map((t) => t.name));
    const scopeKey = scopeUrlPart(currentScope);
    const rowLoads = useRowLoads({enabled: rowLoadProgress, scopeKey, viewingName, queuedLoadNames});
    const enqueueLoad: typeof queueEnqueue = rowLoadProgress
        ? (task) => {
            rowLoads.beforeEnqueue(task.name);
            queueEnqueue(task);
        }
        : queueEnqueue;
    // Source name of the FEA picker modal, or null if closed. Only one
    // picker open at a time matches the file-list interaction model.
    const [pickerName, setPickerName] = useState<string | null>(null);
    const folderTree = useFolderTree(scopeKey, files);
    const {expandedFolders, setExpandedFolders, toggleFolder, pendingFolders, setPendingFolders,
        removePendingFoldersUnder, newFolderAt, setNewFolderAt} = folderTree;

    // Right-click context menu: items are computed at open time by the
    // same builders that feed the kebab, so the two stay in lockstep.
    const [ctxMenu, setCtxMenu] = useState<{
        x: number;
        y: number;
        items: KebabMenuItem[];
        header?: React.ReactNode;
    } | null>(null);
    const openCtxMenu = (
        e: {clientX: number; clientY: number; preventDefault?: () => void; stopPropagation?: () => void},
        items: KebabMenuItem[],
        header?: React.ReactNode,
    ) => {
        if (items.length === 0) return;
        e.preventDefault?.();
        e.stopPropagation?.();
        setCtxMenu({x: e.clientX, y: e.clientY, items, header});
    };
    // Mutating actions (delete / rename / move): personal scope for
    // everyone via the user endpoints, admins elsewhere via the admin
    // endpoints. The backend enforces the same split; canMutate just
    // keeps dead-end affordances out of the UI.
    const mutations = useStorageMutations();
    const canMutate = mutations.canMutate;
    const [picker, setPicker] = useState<FolderPicker | null>(null);
    // "+" menu (upload files / new folder).
    const [plusOpen, setPlusOpen] = useState(false);
    const plusBtnRef = useRef<HTMLButtonElement>(null);

    const uploads = useUploads({setPicker});
    const {uploading, fileInputRef, uploadTargetRef, uploadFilesTo} = uploads;

    const procedural = useProceduralModels({scopeKey, loadedSourceNames, canMutate, setPicker, setPlusOpen});
    const {
        proceduralModels, proceduralByName, activeProcedural, refreshProceduralModels,
        isProceduralLoaded, toggleProceduralLoaded, applyProceduralName, placementItems,
    } = procedural;
    const activeModel = useCellBuilderStore((s) => s.active);
    const unsavedName = unsavedModelName(activeModel, proceduralModels);

    // Sticky 600ms spin window for the Refresh button so a tap is
    // visually acknowledged even though the underlying list-files
    // request is fire-and-forget over websocket. Without this the
    // icon never changes state on mobile and the tap feels dead.
    const [refreshing, setRefreshing] = useState(false);
    const refreshTimerRef = useRef<number | null>(null);
    const onRefresh = () => {
        if (refreshTimerRef.current !== null) {
            window.clearTimeout(refreshTimerRef.current);
            refreshTimerRef.current = null;
        }
        setRefreshing(true);
        void request_list_of_files_from_server();
        void refreshProceduralModels();
        refreshTimerRef.current = window.setTimeout(() => {
            setRefreshing(false);
            refreshTimerRef.current = null;
        }, 600);
    };
    // Clear temp files: one request; the server deletes the scope's regenerable caches (converted
    // formats, utility overlays) natively. Loaded models keep their meshes; the next load
    // reconverts.
    const [clearingTemp, setClearingTemp] = useState(false);
    const onClearTemp = async () => {
        if (clearingTemp) return;
        if (!await confirmDialog({
            title: "Clear temp files in this scope?",
            body: "Converted view caches and utility overlays are removed; sources, folders and " +
                "procedural models stay. Models are reconverted the next time they load.",
            confirmLabel: "Clear temp files",
            tone: "danger",
        })) return;
        setClearingTemp(true);
        try {
            const {deleted, errors} = await mutations.clearTemp();
            void alertDialog({
                title: `Cleared ${deleted} temp file${deleted === 1 ? "" : "s"}.`,
                body: errors.length ? [`${errors.length} could not be removed:`, ...errors.slice(0, 10)] : undefined,
            });
        } catch (e) {
            void alertDialog({title: "Clearing temp files failed", body: errorText(e), tone: "danger"});
        } finally {
            setClearingTemp(false);
            void request_list_of_files_from_server();
        }
    };
    // Cancel a pending spin-window callback if the panel unmounts
    // while we're still in the visible-busy hold.
    useEffect(() => () => {
        if (refreshTimerRef.current !== null) {
            window.clearTimeout(refreshTimerRef.current);
        }
    }, []);

    const fileOps = useFileOps({
        files, scopeKey, mutations, loadedSourceNames, setExpandedFolders, setPendingFolders,
        removePendingFoldersUnder, clearSelection, setPicker, setNewFolderAt,
    });
    const {
        renaming, setRenaming, onDownloadFile, alertError, opNote, moveKeysWithProgress,
        onMoveSingleToFolder, runFolderMove, onMoveFolderInto, onRenameFolderCommit,
        onRenameFileCommit, unloadIfLoaded, onDeleteFile, onDeleteFolder, onCreateFolder,
    } = fileOps;

    // ── Keyboard navigation over the visible (regular) tree ────────
    // Flattened render order of the rows currently on screen; versions
    // subtree is excluded (its own collapsing structure).
    const {regular: classifiedRegular, branches: versionBranches} = classifyFiles(files, sidecars);

    // Procedural models are database rows, not blobs — but an operator files
    // them beside real sources and reasonably wants them in the same folders.
    // Their NAME carries the path (see procedural.normalize_model_name), so
    // feeding them through the same tree builder puts them exactly where the
    // name says, with no second hierarchy to keep in step.
    //
    // Synthesised AFTER classifyFiles: they are not version-branch artefacts,
    // and nothing downstream reads fileType on them — the renderer switches on
    // the name being a known model and draws a model row instead.
    const regularFiles = useMemo(() => {
        const synthetic: ServerFileEntry[] = proceduralModels.map((m) => ({
            name: m.name,
            fileType: 0 as ServerFileEntry["fileType"],
            filepath: m.name,
            lastModified: "",
        }));
        return [...classifiedRegular, ...synthetic];
    }, [classifiedRegular, proceduralModels]);
    const regular = regularFiles;
    const visibleTree = buildFileTree(regularFiles, (f) => f.name, pendingFolders);

    const bulk = useBulkActions({
        files, scopeKey, selection, clearSelection, loadedSourceNames, enqueueLoad, removeQueuedLoad,
        queuedLoadNames, viewingName, proceduralByName, isProceduralLoaded, toggleProceduralLoaded,
        refreshProceduralModels, applyProceduralName, mutations, unloadIfLoaded, moveKeysWithProgress,
        alertError, setPicker, bulkBusy, setBulkBusy,
    });
    const {onToggle, onLoadStreamer, onLoadSelected, onUnloadSelected, onDeleteSelected, onMoveSelected, onHideAll} = bulk;

    const selectionFor = (name: string): SelectionMenuContext | undefined => {
        if (selectionActions !== "rowMenu" || !selection.has(name)) return undefined;
        const keys = Array.from(selection);
        const models = keys.map((k) => proceduralByName.get(k)).filter((m): m is ProceduralModelSummary => !!m);
        return {
            count: selection.size,
            hasVersions: keys.some((k) => k.replace(/^\/+/, "").startsWith("versions/")),
            busy: bulkBusy !== null,
            canLoad: selection.size - models.length + models.filter((m) => !!m.latest_glb_key).length > 0,
            onLoad: onLoadSelected,
            onUnload: onUnloadSelected,
            onMove: canMutate ? onMoveSelected : undefined,
            onDelete: canMutate ? () => void onDeleteSelected() : undefined,
            onClear: clearSelection,
        };
    };
    const proceduralMenuItems = (m: ProceduralModelSummary, displayName: string): KebabMenuItem[] => {
        const items = procedural.proceduralMenuItems(m, displayName);
        return selectionActions === "rowMenu" ? [...items, ...selectionMenuItems(selectionFor(m.name))] : items;
    };

    const dnd = useDragAndMenus({
        selection, canMutate, loadedSourceNames, viewingName, placementItems, runFolderMove,
        moveKeysWithProgress, uploadFilesTo, onToggle, onLoadStreamer, onDownloadFile, setRenaming,
        onMoveSingleToFolder, onDeleteFile, uploadTargetRef, fileInputRef, setNewFolderAt,
        setExpandedFolders, onMoveFolderInto, onDeleteFolder, onConvert,
        cancelLoadFor: rowLoadProgress ? rowLoads.cancelFor : undefined,
        dismissLoadFor: rowLoadProgress ? rowLoads.dismissFor : undefined,
        selectionFor: selectionActions === "rowMenu" ? selectionFor : undefined,
    });

    const nav = useListKeyboardNav({
        visibleTree, files, expandedFolders, toggleFolder, selection, setSelection, toggleSelection,
        lastSelectedRef, loadedSourceNames, queuedLoadNames, canMutate, onToggle, onDeleteSelected,
        onDeleteFile, onDeleteFolder,
    });
    const {focusedKey, setFocusedKey, flatRows} = nav;

    // "auto": the Modified column needs the room; measured on the list's container.
    // The list mounts and unmounts with the files, so the observed element is checked after every render.
    const [narrow, setNarrow] = useState(false);
    const observed = useRef<{el: Element | null; ro: ResizeObserver | null}>({el: null, ro: null});
    useEffect(() => {
        const o = observed.current;
        const el = showModified === "auto"
            ? nav.listScrollRef.current?.parentElement ?? nav.listScrollRef.current
            : null;
        if (el === o.el) return;
        o.ro?.disconnect();
        o.el = el;
        o.ro = null;
        if (!el || typeof ResizeObserver === "undefined") return;
        o.ro = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < STORAGE_MODIFIED_MIN_PX));
        o.ro.observe(el);
    });
    useEffect(() => () => observed.current.ro?.disconnect(), []);
    const modifiedShown = showModified === "auto" ? !narrow : showModified;

    // Row selection, shared by file rows and procedural-model rows. Both are
    // leaves of the same tree, so shift-range has to run over the same visible
    // order — a range that skipped models would select around them.
    const onRowSelectToggle = (name: string, shiftKey?: boolean) => {
        setFocusedKey(`file:${name}`);
        if (shiftKey && lastSelectedRef.current && lastSelectedRef.current !== name) {
            const rowNames = flatRows
                .filter((r) => r.kind === "file")
                .map((r) => (r as {name: string}).name);
            const a = rowNames.indexOf(lastSelectedRef.current);
            const b = rowNames.indexOf(name);
            if (a >= 0 && b >= 0) {
                const [lo, hi] = a < b ? [a, b] : [b, a];
                setSelection((prev) => {
                    const next = new Set(prev);
                    for (let i = lo; i <= hi; i++) next.add(rowNames[i]);
                    return next;
                });
                lastSelectedRef.current = name;
                return;
            }
        }
        lastSelectedRef.current = name;
        toggleSelection(name);
    };

    const existingFolderPaths = Array.from(
        new Set([...collectFolderPaths(files, (f) => f.name), ...pendingFolders]),
    ).sort((a, b) => a.localeCompare(b));

    return (
        <>
            <BrowserHeader
                currentScope={currentScope}
                hiddenFiles={hiddenFiles}
                fileInputRef={fileInputRef}
                importXlsxInputRef={uploads.importXlsxInputRef}
                onFilePicked={uploads.onFilePicked}
                uploading={uploading}
                plusOpen={plusOpen}
                setPlusOpen={setPlusOpen}
                plusBtnRef={plusBtnRef}
                templatesOpen={procedural.templatesOpen}
                setTemplatesOpen={procedural.setTemplatesOpen}
                templatesBtnRef={procedural.templatesBtnRef}
                allTemplates={procedural.allTemplates}
                importPrompt={procedural.importPrompt}
                importEngines={procedural.importEngines}
                activeProcedural={activeProcedural}
                createProceduralModel={procedural.createProceduralModel}
                createProceduralModelFromTemplate={procedural.createProceduralModelFromTemplate}
                setNewFolderAt={setNewFolderAt}
                onRefresh={onRefresh}
                refreshing={refreshing}
                onClearTemp={mutations.canClearTemp ? () => void onClearTemp() : null}
                clearingTemp={clearingTemp}
                anyLoaded={anyLoaded}
                bulkBusy={bulkBusy}
                onHideAll={onHideAll}
                onClose={onClose}
                scopePicker={scopePicker}
            />
            {inSelectionMode && selectionActions === "toolbar" && (
                <SelectionToolbar
                    selection={selection}
                    proceduralByName={proceduralByName}
                    bulkBusy={bulkBusy}
                    canMutate={canMutate}
                    onLoadSelected={onLoadSelected}
                    onUnloadSelected={onUnloadSelected}
                    onMoveSelected={onMoveSelected}
                    onDeleteSelected={onDeleteSelected}
                    clearSelection={clearSelection}
                />
            )}
            <UploadProgress
                opNote={opNote}
                uploadName={uploads.uploadName}
                uploadLoaded={uploads.uploadLoaded}
                uploadTotal={uploads.uploadTotal}
            />
            {unsavedName !== null && <UnsavedModelRow name={unsavedName}/>}
            <FileTreeList
                files={files}
                regularFiles={regularFiles}
                versionBranches={versionBranches}
                visibleTree={visibleTree}
                pendingFolders={pendingFolders}
                sidecars={sidecars}
                showModified={modifiedShown}
                fillHeight={fillHeight}
                loadProgressFor={rowLoadProgress ? rowLoads.progressFor : undefined}
                listScrollRef={nav.listScrollRef}
                onListKeyDown={nav.onListKeyDown}
                handleDropOnFolder={dnd.handleDropOnFolder}
                showRootDropStrip={dnd.showRootDropStrip}
                newFolderAt={newFolderAt}
                setNewFolderAt={setNewFolderAt}
                onCreateFolder={onCreateFolder}
                proceduralByName={proceduralByName}
                activeProcedural={activeProcedural}
                openProceduralModel={procedural.openProceduralModel}
                isProceduralLoaded={isProceduralLoaded}
                toggleProceduralLoaded={toggleProceduralLoaded}
                proceduralMenuItems={proceduralMenuItems}
                fileMenuItems={dnd.fileMenuItems}
                versionFileMenuItems={dnd.versionFileMenuItems}
                folderMenuItems={dnd.folderMenuItems}
                openCtxMenu={openCtxMenu}
                selection={selection}
                onRowSelectToggle={onRowSelectToggle}
                toggleSelection={toggleSelection}
                focusedKey={focusedKey}
                setFocusedKey={setFocusedKey}
                viewingName={viewingName}
                loadedSourceNames={loadedSourceNames}
                conversionJobs={conversionJobs}
                expandedName={expandedName}
                setExpandedName={setExpandedName}
                onToggle={onToggle}
                setPickerName={setPickerName}
                queuedLoadNames={queuedLoadNames}
                canMutate={canMutate}
                onDragStartFile={dnd.onDragStartFile}
                onDragEndFile={dnd.onDragEndFile}
                uploadFilesTo={uploadFilesTo}
                draggingKeys={dnd.draggingKeys}
                setDraggingFolder={dnd.setDraggingFolder}
                renaming={renaming}
                setRenaming={setRenaming}
                onRenameFileCommit={onRenameFileCommit}
                onRenameFolderCommit={onRenameFolderCommit}
                expandedFolders={expandedFolders}
                toggleFolder={toggleFolder}
                setGitHistoryOpen={setGitHistoryOpen}
            />
            <BrowserDialogs
                ctxMenu={ctxMenu}
                setCtxMenu={setCtxMenu}
                pickerName={pickerName}
                setPickerName={setPickerName}
                gitHistoryOpen={gitHistoryOpen}
                setGitHistoryOpen={setGitHistoryOpen}
                files={files}
                loadedSourceNames={loadedSourceNames}
                viewingName={viewingName}
                onToggle={onToggle}
                picker={picker}
                setPicker={setPicker}
                existingFolderPaths={existingFolderPaths}
            />
        </>
    );
};

/** The classic frame: a menu-column card. */
const StorageBrowser: React.FC = () => (
    <PanelCard
        data-no-upload-menu
        // Match the ObjectInfoBox footprint (viewport-clamped max-width so the
        // panel self-contains on mobile). Mobile: bound the panel to the viewport
        // and SCROLL its content, so a long file list can't run the panel past the
        // bottom of the screen. Desktop keeps the natural unbounded block layout.
        className={
            "w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md " +
            "max-h-[calc(100dvh-6rem)] overflow-y-auto md:max-h-none md:overflow-visible"
        }
    >
        <StorageBody/>
    </PanelCard>
);
export default StorageBrowser;
