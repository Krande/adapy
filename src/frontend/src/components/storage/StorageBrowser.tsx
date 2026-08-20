import {PANEL_CHROME} from "@/state/themeStore";
import React, {useEffect, useRef, useState} from "react";
import ScopePicker from "@/shell/ScopePicker";
import {buttonClasses} from "@/components/ui";
import {createPortal} from "react-dom";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {useConversionStore} from "@/state/conversionStore";
import {useModelState} from "@/state/modelState";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {runtime} from "@/runtime/config";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import ReloadIcon from "../icons/ReloadIcon";
import PlusIcon from "../icons/PlusIcon";
import ExpandIcon from "../icons/ExpandIcon";
import ViewIcon from "../icons/ViewIcon";
import FolderClosedIcon from "../icons/FolderClosedIcon";
import FieldPickerModal from "./FieldPickerModal";
import GitHistoryPanel from "./GitHistoryPanel";
import {useBuildSidecars} from "@/hooks/useBuildSidecars";
import {buildFileTree, collectFolderPaths, loadExpandedFolders, loadPendingFolders, previewKeyList, saveExpandedFolders, savePendingFolders} from "@/utils/storage/fileTree";
import InlineNameInput from "@/components/common/InlineNameInput";
import PositionedMenu, {KebabMenuItem} from "@/components/common/PositionedMenu";
import FolderPickerModal from "@/components/common/FolderPickerModal";
import {viewerApi, type ProceduralModelSummary, type ProceduralTemplate} from "@/services/viewerApi";
import ProceduralModelIcon from "../icons/ProceduralModelIcon";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useStorageMutations} from "./useStorageMutations";
import {useLoadQueueStore} from "@/state/loadQueueStore";
import {buildFileMenuItems, buildFolderMenuItems} from "./storageMenuItems";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {canLoadIntoSceneLegacy, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";
import {KEYS_MIME, FOLDER_MIME, basenameOf, countFiles, dirnameOf, formatBytes, type ServerFileTreeNode} from "./storageHelpers";
import {Spinner} from "./Spinner";
import {newProceduralModel} from "@/shell/buildActions";
import {classifyFiles} from "./classifyFiles";
import {FolderRow} from "./FolderRow";
import {FileRow} from "./FileRow";
import {VersionsTree} from "./VersionsTree";



export interface StorageBrowserProps {
    /**
     * Drop the panel frame and the redundant title.
     *
     * The shell's dock already draws a border, a background and a header carrying this
     * panel's name — so the classic frame produced a box inside a box with two
     * scrollbars, and the <h2>Storage</h2> restated the dock tab directly above it. The
     * SCOPE line stays in both: it is information about which space the list reflects,
     * not decoration.
     *
     * Maximize survives in both, because "give this the whole window" is useful wherever
     * the panel lives.
     */
    chromeless?: boolean;
}

const StorageBrowser: React.FC<StorageBrowserProps> = ({chromeless = false}) => {
    const files = useServerInfoStore((s) => s.serverFileObjects);
    const {sidecars} = useBuildSidecars(files);
    const conversionJobs = useConversionStore((s) => s.jobs);
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);
    const anyLoaded = loadedSourceNames.size > 0;
    const currentScope = useScopeStore((s) => s.current);
    const [uploading, setUploading] = useState(false);
    // Active "Show all" run — disables the per-row toggles while we're
    // overlaying every file in sequence, so the user can't kick off a
    // second batch on top of the first.
    const [bulkBusy, setBulkBusy] = useState<"load" | "unload" | "clear" | "delete" | null>(null);
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
    // Upload progress: name = current file (or null), loaded/total in
    // bytes. Total may stay 0 if the browser can't determine it (rare
    // for File uploads); we treat that as indeterminate.
    const [uploadName, setUploadName] = useState<string | null>(null);
    const [uploadLoaded, setUploadLoaded] = useState(0);
    const [uploadTotal, setUploadTotal] = useState(0);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    // Scene loads run through the sequential load queue; the row
    // spinner tracks whichever model the queue is currently loading.
    const loadCurrent = useLoadQueueStore((s) => s.current);
    const loadQueued = useLoadQueueStore((s) => s.queued);
    const enqueueLoad = useLoadQueueStore((s) => s.enqueue);
    const removeQueuedLoad = useLoadQueueStore((s) => s.removeQueued);
    const viewingName = loadCurrent?.name ?? null;
    const queuedLoadNames = new Set(loadQueued.map((t) => t.name));
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
    // Cancel a pending spin-window callback if the panel unmounts
    // while we're still in the visible-busy hold.
    useEffect(() => () => {
        if (refreshTimerRef.current !== null) {
            window.clearTimeout(refreshTimerRef.current);
        }
    }, []);
    // Source name of the FEA picker modal, or null if closed. Only one
    // picker open at a time matches the file-list interaction model.
    const [pickerName, setPickerName] = useState<string | null>(null);
    // Folder expand state for the regular-files tree, keyed by folder
    // path ("a/b/c"). Default: empty Set = everything collapsed,
    // matching the user-requested behaviour. Persisted per-scope so
    // expand state survives reloads but doesn't leak across scopes.
    const scopeKey = scopeUrlPart(currentScope);
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
        () => loadExpandedFolders("storage", scopeKey),
    );
    // Reset to the per-scope set whenever the active scope changes.
    useEffect(() => {
        setExpandedFolders(loadExpandedFolders("storage", scopeKey));
    }, [scopeKey]);
    // Persist on every change. Cheap — Set is small.
    useEffect(() => {
        saveExpandedFolders("storage", scopeKey, expandedFolders);
    }, [scopeKey, expandedFolders]);
    const toggleFolder = (path: string) => {
        setExpandedFolders((prev) => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };

    // Procedural cell models: pg-backed pseudo-entries listed above the file
    // tree. Each row refers to the single postgres source, not a blob — no
    // rename/move; delete archives the row server-side.
    const [proceduralModels, setProceduralModels] = useState<ProceduralModelSummary[]>([]);
    const activeProcedural = useCellBuilderStore((s) => s.active?.modelId ?? null);
    // Engine-picker prompt raised by the store when an imported workbook has no
    // _ADA_META engine (hand-made / legacy). Rendered here because import is now
    // triggered from the + menu, not the cellbuilder panel.
    const importPrompt = useCellBuilderStore((s) => s.importPrompt);
    const importEngines = useCellBuilderStore((s) => s.engines);
    const refreshProceduralModels = React.useCallback(async () => {
        try {
            setProceduralModels(await viewerApi.listProceduralModels(scopeKey));
        } catch {
            // shared-only deployments (503) or older APIs: hide the section
            setProceduralModels([]);
        }
    }, [scopeKey]);
    useEffect(() => {
        void refreshProceduralModels();
    }, [refreshProceduralModels]);
    // A model becoming active (created / opened / imported) may be new to the
    // list — refresh so an Excel-imported model appears without a manual reload.
    useEffect(() => {
        if (activeProcedural) void refreshProceduralModels();
    }, [activeProcedural, refreshProceduralModels]);

    // Start-from templates for the "New model from template" dropdown — the
    // union of the demo templates advertised by every currently-live worker
    // (base worker → adapy-default; a capability worker → its loft demos, etc.).
    // Refetched on scope change; empty when no workers are up.
    const [allTemplates, setAllTemplates] = useState<ProceduralTemplate[]>([]);
    const [templatesOpen, setTemplatesOpen] = useState(false);
    const templatesBtnRef = useRef<HTMLButtonElement | null>(null);
    const refreshTemplates = React.useCallback(async () => {
        try {
            setAllTemplates(await viewerApi.listProceduralTemplates(scopeKey));
        } catch {
            setAllTemplates([]);
        }
    }, [scopeKey]);
    useEffect(() => {
        void refreshTemplates();
    }, [refreshTemplates]);

    const openProceduralModel = async (m: ProceduralModelSummary) => {
        try {
            const detail = await viewerApi.getProceduralModel(scopeKey, m.id);
            useCellBuilderStore.getState().open(detail.id, detail.name, detail.revision, detail.doc);
            // The model now owns the screen (the cellbuilder panel opens) — collapse
            // the storage overview so it doesn't sit on top of the freshly-opened model.
            useServerInfoStore.getState().setShowServerInfoBox(false);
        } catch (e) {
            window.alert(`Failed to open procedural model: ${e instanceof Error ? e.message : e}`);
        }
    };

    // Delegates to the shared action, which the Build toolbar and the File menu also
    // call. Creating a model is a File-menu operation everywhere else in software, and
    // the mode you create one FOR is Build — this menu should not be its only home, and
    // three doors onto three implementations is how they drift.
    const createProceduralModel = async () => {
        await newProceduralModel();
        void refreshProceduralModels();
    };

    // Instantiate a new model from a template: commit the template's document
    // verbatim (so loft members / systems survive untouched — the cellbuilder's
    // box round-trip would drop them), then kick a compile and open it. The
    // committed doc's engine is mirrored onto the model, so a worker-backed
    // template auto-routes its compile to that worker.
    const createProceduralModelFromTemplate = async (tpl: ProceduralTemplate) => {
        setTemplatesOpen(false);
        setPlusOpen(false);
        const name = window.prompt("Name for the new procedural model:", tpl.name);
        if (!name || !name.trim()) return;
        try {
            const detail = await viewerApi.createProceduralModel(scopeKey, name.trim());
            const {revision} = await viewerApi.commitProceduralModel(scopeKey, detail.id, tpl.doc, detail.revision);
            // Compile so the model has a rendered GLB immediately; ignore compile
            // errors here (the model still opens and can be recompiled).
            try {
                await viewerApi.compileProceduralModel(scopeKey, detail.id);
            } catch {
                /* compile is best-effort on create */
            }
            const fresh = await viewerApi.getProceduralModel(scopeKey, detail.id);
            useCellBuilderStore
                .getState()
                .open(fresh.id, fresh.name, revision, fresh.doc);
            void refreshProceduralModels();
        } catch (e) {
            window.alert(`Failed to create from template: ${e instanceof Error ? e.message : e}`);
        }
    };

    const deleteProceduralModel = async (m: ProceduralModelSummary) => {
        if (!window.confirm(`Delete procedural model "${m.name}"?`)) return;
        try {
            await viewerApi.deleteProceduralModel(scopeKey, m.id);
            const st = useCellBuilderStore.getState();
            if (st.active?.modelId === m.id) st.close();
            void refreshProceduralModels();
        } catch (e) {
            window.alert(`Failed to delete: ${e instanceof Error ? e.message : e}`);
        }
    };

    // Client-side "pending" empty folders — storage is prefix-based so
    // they have no server representation until a file lands in them.
    // Persisted per-scope; pruned once a real key appears underneath.
    const [pendingFolders, setPendingFolders] = useState<string[]>(
        () => loadPendingFolders("storage", scopeKey),
    );
    useEffect(() => {
        setPendingFolders(loadPendingFolders("storage", scopeKey));
    }, [scopeKey]);
    useEffect(() => {
        savePendingFolders("storage", scopeKey, pendingFolders);
    }, [scopeKey, pendingFolders]);
    useEffect(() => {
        setPendingFolders((prev) => {
            const next = prev.filter(
                (p) => !files.some((f) => f.name.replace(/^\/+/, "").startsWith(p + "/")),
            );
            return next.length === prev.length ? prev : next;
        });
    }, [files]);
    const removePendingFoldersUnder = (path: string) => {
        setPendingFolders((prev) =>
            prev.filter((p) => p !== path && !p.startsWith(path + "/")),
        );
    };

    // Where the "new folder" inline input is showing: "" = top level,
    // a folder path = subfolder of it, null = hidden.
    const [newFolderAt, setNewFolderAt] = useState<string | null>(null);
    // Inline rename target (replaces the old window.prompt flow).
    const [renaming, setRenaming] = useState<{kind: "file" | "folder"; path: string} | null>(null);
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
    // In-panel drag state: keys being dragged (for row dimming + the
    // move-to-root strip). Cleared on dragend/drop.
    const [draggingKeys, setDraggingKeys] = useState<string[] | null>(null);
    const [draggingFolder, setDraggingFolder] = useState<string | null>(null);
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
    // Maximize: same component, restyled as a centered fixed overlay
    // with a backdrop. Styling-only so every bit of panel state
    // (selection, expansion, menus) survives the toggle.
    const [maximized, setMaximized] = useState(false);
    useEffect(() => {
        if (!maximized) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") setMaximized(false);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [maximized]);

    // Mutating actions (delete / rename / move): personal scope for
    // everyone via the user endpoints, admins elsewhere via the admin
    // endpoints. The backend enforces the same split; canMutate just
    // keeps dead-end affordances out of the UI.
    const mutations = useStorageMutations();
    const canMutate = mutations.canMutate;

    // The picker modal drives the move flows and the upload-destination
    // prompt; ``onPick`` is the closure that knows what to do once a
    // destination is chosen.
    const [picker, setPicker] = useState<{
        title: string;
        allowRoot?: boolean;
        submitLabel?: string;
        onPick: (folder: string) => Promise<void> | void;
    } | null>(null);
    // Download a stored blob with auth (REST mode). The suggested filename is the
    // key's basename so nested keys don't save as "a/b/c.ifc".
    const onDownloadFile = (key: string) => {
        void viewerApi.downloadBlob(scopeKey, key, basenameOf(key));
    };

    const alertError = (e: unknown) => {
        window.alert(e instanceof Error ? e.message : String(e));
    };

    // In-flight move status — a spinner line under the header so a
    // drag-drop of many files visibly runs until the listing refreshes.
    // Moves are chunked purely so the counter ticks between requests;
    // every chunk is still a server-side S3 rename (CopyObject+Delete
    // on Garage) — no file bytes pass through the browser. The ref
    // rejects overlapping batches (concurrent moves would race on the
    // server-side collision checks).
    const [opNote, setOpNote] = useState<string | null>(null);
    const opBusyRef = useRef(false);
    const OP_CHUNK = 8;
    const moveKeysWithProgress = async (keys: string[], folder: string) => {
        if (opBusyRef.current || keys.length === 0) return;
        opBusyRef.current = true;
        const label = folder ? `${folder}/` : "root /";
        setOpNote(`Moving 0/${keys.length} to ${label}…`);
        try {
            if (folder === "") {
                // Move-to-root: the move endpoint requires a non-empty
                // folder, so root moves are per-key renames to the
                // basename.
                let done = 0;
                for (const k of keys) {
                    setOpNote(`Moving ${done + 1}/${keys.length} to ${label}…`);
                    await mutations.renameKey(k, basenameOf(k));
                    done++;
                }
            } else {
                const failed: Array<{key: string; reason: string}> = [];
                for (let i = 0; i < keys.length; i += OP_CHUNK) {
                    const chunk = keys.slice(i, i + OP_CHUNK);
                    setOpNote(`Moving ${Math.min(i + chunk.length, keys.length)}/${keys.length} to ${label}…`);
                    const r = await mutations.moveKeys(chunk, folder);
                    failed.push(...r.failed);
                }
                if (failed.length > 0) {
                    window.alert(failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
                }
            }
            clearSelection();
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        } finally {
            opBusyRef.current = false;
            setOpNote(null);
        }
    };

    const onMoveSingleToFolder = (key: string) => {
        setPicker({
            title: `Move "${key}" to folder`,
            onPick: (folder) => moveKeysWithProgress([key], folder),
        });
    };

    const runFolderMove = async (folderPath: string, newPath: string) => {
        if (newPath === folderPath) return;
        if (opBusyRef.current) return;
        opBusyRef.current = true;
        const allKeys = files.map((f) => f.name);
        const count = allKeys.filter((k) => k.replace(/^\/+/, "").startsWith(folderPath + "/")).length;
        setOpNote(`Moving folder "${folderPath}" → "${newPath}" (${count} file${count === 1 ? "" : "s"})…`);
        try {
            const r = await mutations.renameOrMoveFolder(folderPath, newPath, allKeys);
            if (r.failed.length > 0) {
                window.alert(r.failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
            }
            setExpandedFolders((prev) => {
                const next = new Set(prev);
                next.delete(folderPath);
                next.add(newPath);
                return next;
            });
            removePendingFoldersUnder(folderPath);
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        } finally {
            opBusyRef.current = false;
            setOpNote(null);
        }
    };

    const onMoveFolderInto = (folderPath: string) => {
        const basename = basenameOf(folderPath);
        setPicker({
            title: `Move folder "${folderPath}" into`,
            onPick: async (dest) => {
                await runFolderMove(folderPath, `${dest}/${basename}`);
            },
        });
    };

    const onRenameFolderCommit = (folderPath: string, rawName: string, isPending: boolean) => {
        setRenaming(null);
        const name = rawName.trim().replace(/^\/+|\/+$/g, "");
        if (!name || name === basenameOf(folderPath)) return;
        if (name.includes("/")) {
            window.alert("Rename must be a single name; use Move folder into… for nested moves");
            return;
        }
        const parent = dirnameOf(folderPath);
        const newPath = parent ? `${parent}/${name}` : name;
        if (isPending) {
            // No server keys yet — rename is pure client state.
            setPendingFolders((prev) => prev.map((p) => (p === folderPath ? newPath : p)));
            setExpandedFolders((prev) => {
                const next = new Set(prev);
                next.delete(folderPath);
                next.add(newPath);
                return next;
            });
            return;
        }
        void runFolderMove(folderPath, newPath);
    };

    const onRenameFileCommit = async (f: ServerFileEntry, rawName: string) => {
        setRenaming(null);
        const name = rawName.trim();
        if (!name || name === basenameOf(f.name)) return;
        if (name.includes("/")) {
            window.alert("Name must not contain '/' — use Move to folder… instead");
            return;
        }
        const dir = dirnameOf(f.name);
        const newKey = dir ? `${dir}/${name}` : name;
        try {
            // Unload first — the scene's source registry is keyed by
            // name, and a renamed source would leave a stale entry.
            if (loadedSourceNames.has(f.name)) await unload_any_source(f.name);
            await mutations.renameKey(f.name, newKey);
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        }
    };

    const unloadIfLoaded = async (name: string) => {
        if (!loadedSourceNames.has(name)) return;
        await unload_any_source(name);
    };

    const onDeleteFile = async (f: ServerFileEntry) => {
        if (!window.confirm(`Delete "${f.name}"?\nConverted view caches are removed too.`)) return;
        try {
            await unloadIfLoaded(f.name);
            await mutations.deleteKey(f.name);
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        }
    };

    const onDeleteFolder = async (path: string, fileCount: number, isPending: boolean) => {
        if (isPending && fileCount === 0) {
            removePendingFoldersUnder(path);
            return;
        }
        const prefix = path + "/";
        const targets = files.filter((x) => x.name.replace(/^\/+/, "").startsWith(prefix));
        if (!window.confirm(
            `Delete folder "${path}" and its ${fileCount} file${fileCount === 1 ? "" : "s"}?\n` +
            "Converted view caches are removed too.\n\n" +
            previewKeyList(targets.map((t) => t.name)),
        )) return;
        try {
            // Sequential: each delete cascades derived blobs server-side
            // and parallel calls would race on the storage listing.
            for (const t of targets) {
                await unloadIfLoaded(t.name);
                await mutations.deleteKey(t.name);
            }
            removePendingFoldersUnder(path);
            setExpandedFolders((prev) => {
                const next = new Set(prev);
                next.delete(path);
                return next;
            });
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        }
    };

    const onCreateFolder = (parent: string, rawName: string) => {
        setNewFolderAt(null);
        const name = rawName.trim().replace(/^\/+|\/+$/g, "");
        if (!name) return;
        if (name.includes("/")) {
            window.alert("Folder name must not contain '/'");
            return;
        }
        const path = parent ? `${parent}/${name}` : name;
        if (!parent && (name === "versions" || name === "_derived")) {
            window.alert(`"${name}" is a reserved name`);
            return;
        }
        setPendingFolders((prev) => (prev.includes(path) ? prev : [...prev, path]));
        setExpandedFolders((prev) => {
            const next = new Set(prev);
            if (parent) next.add(parent);
            next.add(path);
            return next;
        });
    };

    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);
    // Hidden picker for "Import from Excel…" in the + menu — imports create a
    // NEW procedural model, so the entry point lives here rather than in the
    // cellbuilder panel (which only exists once a model is open).
    const importXlsxInputRef = useRef<HTMLInputElement>(null);
    // Folder destination for the next picker-initiated upload
    // ("Upload here…" on a folder). Consumed once by onFilePicked.
    const uploadTargetRef = useRef<string | null>(null);
    // "+" menu (upload files / new folder).
    const [plusOpen, setPlusOpen] = useState(false);
    const plusBtnRef = useRef<HTMLButtonElement>(null);

    // Toggle a file in/out of the scene. All adds go through the
    // overlay path so multiple models can coexist; ``Clear`` in
    // the header drops everything if you want a fresh view. The
    // first checked file behaves identically to a normal load
    // (the loader's else branch computes a translation from its
    // bbox); subsequent files reuse that translation so they
    // overlay correctly.
    const onToggle = async (entry: ServerFileEntry, nextChecked: boolean) => {
        if (nextChecked) {
            // Queue the load — more can be queued while one is in
            // flight; the queue drains sequentially (shared loader
            // state can't take concurrent loads).
            enqueueLoad({name: entry.name});
            return;
        }
        if (queuedLoadNames.has(entry.name)) {
            removeQueuedLoad(entry.name);
            return;
        }
        if (viewingName === entry.name) return; // mid-load; can't cancel
        try {
            await unload_any_source(entry.name);
        } catch (err) {
            console.error("storage toggle failed", err);
        }
    };

    // Load a STEP file via the memory-bounded streaming converter (one solid at a
    // time) — for large assemblies whose normal OCC->GLB conversion OOM-kills the
    // worker. Same overlay flow as onToggle, with the streamer flag set.
    const onLoadStreamer = (name: string) => {
        enqueueLoad({name, streamer: true});
    };

    // Bulk "show all" — overlay every file currently absent from the
    // scene. Sequential (not parallel) because overlay_file_in_scene
    // shares loader state and races corrupt the scene; the per-row
    // viewingName indicator follows along so the user sees progress.
    // Apply load/unload to the multi-selection set. Sequential
    // because overlay_file_in_scene shares loader state and races
    // would corrupt the scene; we do want to load even
    // already-loaded items (no-op overlay) and unload already-hidden
    // items (no-op unload) so the user gets a predictable result
    // regardless of the per-row state mix.
    const onLoadSelected = () => {
        const targets = files.filter((f) =>
            selection.has(f.name) && !loadedSourceNames.has(f.name) &&
            (isStreamingFEAResult(f.name) || canLoadIntoSceneLegacy(f.name)));
        for (const f of targets) enqueueLoad({name: f.name});
        clearSelection();
    };
    const onUnloadSelected = () => {
        if (bulkBusy !== null) return;
        const targets = files.filter((f) => selection.has(f.name) && loadedSourceNames.has(f.name));
        setBulkBusy("unload");
        try {
            for (const f of targets) {
                try {
                    unload_source_from_scene(f.name);
                } catch (err) {
                    console.error("unload-selected failed", f.name, err);
                }
            }
        } finally {
            setBulkBusy(null);
            clearSelection();
        }
    };

    // Bulk delete / move over the selection set. Version blobs are
    // server-protected (400), so the toolbar disables these when the
    // selection includes any — no silent skipping.
    const onDeleteSelected = async () => {
        if (bulkBusy !== null) return;
        const keys = Array.from(selection);
        if (keys.length === 0) return;
        if (!window.confirm(
            `Delete ${keys.length} file${keys.length === 1 ? "" : "s"}?\n` +
            "Converted view caches are removed too.\n\n" +
            previewKeyList(keys),
        )) return;
        setBulkBusy("delete");
        try {
            // Sequential: deletes cascade derived blobs server-side and
            // parallel calls would race on the storage listing.
            for (const k of keys) {
                await unloadIfLoaded(k);
                await mutations.deleteKey(k);
            }
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        } finally {
            setBulkBusy(null);
            clearSelection();
        }
    };
    const onMoveSelected = () => {
        const keys = Array.from(selection);
        if (keys.length === 0) return;
        setPicker({
            title: `Move ${keys.length} file${keys.length === 1 ? "" : "s"} to folder`,
            onPick: (folder) => moveKeysWithProgress(keys, folder),
        });
    };

    // Drop every loaded source via the canonical teardown.
    // clear_loaded_model resets animation state, tree-view,
    // model-key map, scene groups, and selection in one shot;
    // iterating unload_source_from_scene per file would leave that
    // bookkeeping stale.
    const onHideAll = async () => {
        if (bulkBusy !== null) return;
        setBulkBusy("clear");
        try {
            await clear_loaded_model();
            // Also close any open procedural model — its cellbuilder proxies /
            // compiled result are part of "what's in the scene", so Clear should
            // tear that down too (and hide the cellbuilder panel).
            const cb = useCellBuilderStore.getState();
            if (cb.active) cb.close();
        } catch (err) {
            console.error("clear scene failed", err);
        } finally {
            setBulkBusy(null);
        }
    };

    // Upload a batch sequentially (presigned PUT is per-file); a failed
    // file is collected and reported at the end rather than aborting
    // the batch. ``folder`` prefixes every file's key — used by
    // "Upload here…" and OS-file drops onto a folder row.
    const uploadFilesTo = async (list: File[], folder?: string) => {
        if (list.length === 0) return;
        setUploading(true);
        const failures: string[] = [];
        for (let i = 0; i < list.length; i++) {
            const file = list[i];
            setUploadName(list.length > 1 ? `${file.name} (${i + 1}/${list.length})` : file.name);
            setUploadLoaded(0);
            setUploadTotal(file.size);
            try {
                await uploadFile(file, {
                    folder,
                    onProgress: (loaded, total) => {
                        setUploadLoaded(loaded);
                        if (total) setUploadTotal(total);
                    },
                });
            } catch (err) {
                console.error("upload failed", file.name, err);
                failures.push(file.name);
            }
        }
        setUploading(false);
        setUploadName(null);
        setUploadLoaded(0);
        setUploadTotal(0);
        if (failures.length) window.alert(`Upload failed for: ${failures.join(", ")}`);
    };

    const onFilePicked = (e: React.ChangeEvent<HTMLInputElement>) => {
        const picked = Array.from(e.target.files ?? []);
        e.target.value = "";
        const folder = uploadTargetRef.current;
        uploadTargetRef.current = null;
        if (picked.length === 0) return;
        if (folder !== null) {
            // "Upload here…" on a folder row — destination already chosen.
            void uploadFilesTo(picked, folder || undefined);
            return;
        }
        // Generic "Upload files…": ask where the batch should land — an
        // existing folder, a new path, or the top level (the default).
        setPicker({
            title: `Upload ${picked.length} file${picked.length === 1 ? "" : "s"} to`,
            allowRoot: true,
            submitLabel: "Upload",
            onPick: (dest) => void uploadFilesTo(picked, dest || undefined),
        });
    };

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

    const existingFolderPaths = Array.from(
        new Set([...collectFolderPaths(files, (f) => f.name), ...pendingFolders]),
    ).sort((a, b) => a.localeCompare(b));

    // ── Keyboard navigation over the visible (regular) tree ────────
    // Flattened render order of the rows currently on screen; versions
    // subtree is excluded (its own collapsing structure).
    const {regular: regularFiles, branches: versionBranches} = classifyFiles(files, sidecars);
    const visibleTree = buildFileTree(regularFiles, (f) => f.name, pendingFolders);
    type FlatRow =
        | {kind: "folder"; path: string; depth: number; parent: string}
        | {kind: "file"; name: string; file: ServerFileEntry; depth: number; parent: string};
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

    const showRootDropStrip =
        (draggingKeys !== null && draggingKeys.some((k) => dirnameOf(k) !== "")) ||
        (draggingFolder !== null && dirnameOf(draggingFolder) !== "");

    return (
        <div
            data-no-upload-menu
            // Compact: match ObjectInfoBox footprint (viewport-clamped
            // max-width so the panel self-contains on mobile).
            // Maximized: same element restyled as a centered fixed
            // overlay — styling-only so panel state survives the
            // toggle. The host column has no transform ancestors, so
            // position:fixed escapes it cleanly.
            className={
                (chromeless && !maximized ? "flex min-h-0 flex-1 flex-col " : PANEL_CHROME + " ") +
                (maximized
                    ? "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[61] " +
                      // Same footprint as the floating admin panel
                      // (InViewerPanelHost Rnd: 1100×720 capped to the
                      // viewport). dvh not vh: on mobile 100vh includes
                      // the area behind the browser chrome, so a
                      // vh-sized panel ran past the visible bottom.
                      "w-[min(1100px,calc(100vw-2rem))] h-[min(720px,calc(100dvh-5rem))] flex flex-col"
                    : chromeless
                      ? // In a dock the host owns width and scrolling; imposing a
                        // max-width here would leave a gap the user cannot close by
                        // dragging the splitter, which reads as a broken panel.
                        "min-w-0 overflow-y-auto scrollbar"
                      : // Mobile: bound the panel to the viewport and SCROLL its
                        // content (overflow-y-auto), so a long file list can't run
                        // the panel past the bottom of the screen. Desktop keeps the
                        // natural unbounded block layout (md:max-h-none md:overflow-visible).
                        "w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md " +
                        "max-h-[calc(100dvh-6rem)] overflow-y-auto md:max-h-none md:overflow-visible")
            }
        >
            {maximized && createPortal(
                // Light scrim — just enough to signal modality without
                // blacking out the 3D scene. z-[5]: the panel lives in
                // the menu overlay's `z-10` stacking context, so its
                // own z-index can never exceed 10 at the root level —
                // a body-portaled scrim above 10 paints OVER the panel
                // and darkens it too (visibly so on mobile). Below 10
                // it dims only the canvas underneath.
                <div
                    className="fixed inset-0 z-[5] bg-black/25"
                    onClick={() => setMaximized(false)}
                    aria-hidden="true"
                />,
                document.body,
            )}
            <div className="flex justify-between items-center gap-2 mb-2">
                <div className="min-w-0 flex-1">
                    {/* The dock header already says which panel this is. Repeating it
                        inside was one of four places the same idea was spelled out —
                        Library ▸ Storage ▸ "Storage" ▸ "Refresh file list". */}
                    {!chromeless && <h2 className="font-bold truncate">Storage</h2>}
                    {/* The scope PICKER, not just its name.
                        
                        It was in the title bar, which put it as far from the file list it
                        governs as the window allows. Scope decides which files exist —
                        upload under one and they are invisible under another — so it
                        belongs at the top of the list it filters, the way a folder path
                        does. The title bar kept it visible everywhere; but "visible
                        everywhere" is worth less than "next to the thing it changes",
                        and the Files panel is reachable from every mode now. */}
                    <ScopePicker />
                </div>
                <div className="flex items-center gap-1 shrink-0">
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept={uploadAcceptAttr()}
                        style={{display: "none"}}
                        onChange={onFilePicked}
                    />
                    <input
                        ref={importXlsxInputRef}
                        type="file"
                        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        style={{display: "none"}}
                        onChange={(e) => {
                            const file = e.target.files?.[0];
                            // Reset so re-picking the same file fires onChange again.
                            e.target.value = "";
                            if (file) void useCellBuilderStore.getState().beginImportFromExcel(file);
                        }}
                    />
                    <button
                        ref={plusBtnRef}
                        type="button"
                        className={
                            // Ghost, not accent-filled. Two 40px accent squares in a
                            // panel header read as the loudest thing in the panel, which
                            // put "add a file" and "refresh" above the files themselves.
                            // The 40px floor still applies under a coarse pointer — that
                            // is what IconButton's size classes already do.
                            buttonClasses("ghost", "sm") + " "
                        }
                        onClick={() => setPlusOpen((v) => !v)}
                        disabled={uploading}
                        title="Add — upload files or create a folder"
                        aria-label="Add"
                        aria-haspopup="menu"
                        aria-expanded={plusOpen}
                    >
                        {/* Fixed 24px icon slot — keeps this button the
                            same size as Refresh/Maximize whether it
                            shows the plus or the busy spinner. */}
                        <span className="inline-flex h-6 w-6 items-center justify-center">
                            {uploading ? <Spinner/> : <PlusIcon width="24px" height="24px"/>}
                        </span>
                    </button>
                    {plusOpen && (
                        <PositionedMenu
                            items={[
                                {
                                    key: "upload",
                                    label: "Upload files…",
                                    onClick: () => fileInputRef.current?.click(),
                                },
                                {
                                    key: "new-folder",
                                    label: "New folder…",
                                    onClick: () => setNewFolderAt(""),
                                },
                                {
                                    key: "new-procedural",
                                    label: "New procedural model…",
                                    onClick: () => void createProceduralModel(),
                                },
                                {
                                    key: "import-xlsx",
                                    label: "Import from Excel…",
                                    // Imports create a new procedural model; the
                                    // owning engine is detected from the file's
                                    // _ADA_META, else the user is prompted.
                                    onClick: () => {
                                        setPlusOpen(false);
                                        importXlsxInputRef.current?.click();
                                    },
                                },
                                {
                                    key: "new-from-template",
                                    label: "New model from template ▸",
                                    // Swap the + menu for the template list,
                                    // anchored off the same + button.
                                    onClick: () => {
                                        setPlusOpen(false);
                                        setTemplatesOpen(true);
                                    },
                                },
                            ]}
                            onClose={() => setPlusOpen(false)}
                            ignoreOutsideRef={plusBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    {templatesOpen && (
                        <PositionedMenu
                            header={
                                <span className="text-[11px] uppercase tracking-wide opacity-60">
                                    Start from template
                                </span>
                            }
                            items={allTemplates.map(
                                (tpl): KebabMenuItem => ({
                                    key: tpl.id,
                                    // Engine in parentheses, per request — e.g.
                                    // "Topside + jacket (adapy-default)".
                                    label: `${tpl.name} (${tpl.engine})`,
                                    onClick: () => void createProceduralModelFromTemplate(tpl),
                                }),
                            )}
                            onClose={() => setTemplatesOpen(false)}
                            ignoreOutsideRef={templatesBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    {importPrompt && (
                        <PositionedMenu
                            header={
                                <span className="text-[11px] uppercase tracking-wide opacity-60">
                                    Import “{importPrompt.name}” as…
                                </span>
                            }
                            items={[
                                ...importEngines.map(
                                    (eng): KebabMenuItem => ({
                                        key: eng.slug,
                                        label: eng.name,
                                        onClick: () =>
                                            void useCellBuilderStore
                                                .getState()
                                                // Pass the prompt captured here at
                                                // render time: the menu dismisses
                                                // (cancelImport) before this fires,
                                                // clearing importPrompt in the store.
                                                .confirmImportEngine(eng.slug, importPrompt),
                                    }),
                                ),
                                {
                                    key: "__cancel",
                                    label: "Cancel",
                                    onClick: () => useCellBuilderStore.getState().cancelImport(),
                                },
                            ]}
                            onClose={() => useCellBuilderStore.getState().cancelImport()}
                            ignoreOutsideRef={plusBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    <button
                        type="button"
                        className={buttonClasses("ghost", "sm")}
                        onClick={onRefresh}
                        title={refreshing ? "Refreshing — tap again to retry" : "Refresh file list"}
                        aria-label="Refresh list"
                        aria-busy={refreshing}
                    >
                        <span className={"inline-flex h-6 w-6 items-center justify-center " + (refreshing ? "animate-spin" : "")}>
                            <ReloadIcon/>
                        </span>
                    </button>
                    {/* Clear: unload every loaded source. This is a
                        teardown action (drops the meshes from the
                        scene), distinct from per-element visibility
                        which lives in the Selected Object Info
                        panel. There's no symmetric "Load all" — the
                        user picks the files they want via per-row
                        checkboxes; loading every file at once would
                        rarely be the right thing. */}
                    {(anyLoaded || activeProcedural) && (
                        <button
                            type="button"
                            className={
                                "bg-surface-2 hover:bg-surface-3 active:bg-surface-0 disabled:opacity-60 cursor-pointer " +
                                "text-white rounded-sm text-xs whitespace-nowrap " +
                                "px-2 sm:px-2 py-1 min-h-[40px] sm:min-h-0"
                            }
                            onClick={() => void onHideAll()}
                            disabled={bulkBusy !== null}
                            title="Unload every model in the scene, and close any open procedural model"
                            aria-label="Clear scene"
                            aria-busy={bulkBusy === "clear"}
                        >
                            {bulkBusy === "clear" ? "Clearing…" : "Clear"}
                        </button>
                    )}
                    <button
                        type="button"
                        className={
                            "bg-surface-2 hover:bg-surface-3 active:bg-surface-0 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center " +
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-accent"
                        }
                        onClick={() => setMaximized((v) => !v)}
                        title={maximized ? "Restore compact panel" : "Maximize"}
                        aria-label={maximized ? "Restore compact panel" : "Maximize"}
                    >
                        <span className="inline-flex h-6 w-6 items-center justify-center">
                            <ExpandIcon expanded={maximized} width="24px" height="24px"/>
                        </span>
                    </button>
                </div>
            </div>
            {inSelectionMode && (() => {
                const selectionHasVersions = Array.from(selection).some((k) =>
                    k.replace(/^\/+/, "").startsWith("versions/"),
                );
                const btn = "text-white text-xs px-2 py-1 rounded-sm min-h-[36px] sm:min-h-0 cursor-pointer disabled:opacity-60 disabled:cursor-default";
                return (
                    <div className="mb-2 px-2 py-1.5 rounded-sm border border-edge bg-surface-0 flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-white whitespace-nowrap">
                            {selection.size} selected
                        </span>
                        <button
                            type="button"
                            onClick={onLoadSelected}
                            disabled={bulkBusy !== null}
                            className={`bg-accent hover:bg-accent active:bg-accent-subtle ${btn}`}
                        >
                            Load
                        </button>
                        <button
                            type="button"
                            onClick={onUnloadSelected}
                            disabled={bulkBusy !== null}
                            className={`bg-surface-2 hover:bg-surface-3 active:bg-surface-0 ${btn}`}
                        >
                            {bulkBusy === "unload" ? "Unloading…" : "Unload"}
                        </button>
                        {canMutate && (
                            <button
                                type="button"
                                onClick={onMoveSelected}
                                disabled={bulkBusy !== null || selectionHasVersions}
                                title={selectionHasVersions ? "CI version files can't be moved" : "Move selected files to a folder"}
                                className={`bg-surface-2 hover:bg-surface-3 active:bg-surface-0 ${btn}`}
                            >
                                Move…
                            </button>
                        )}
                        {canMutate && (
                            <button
                                type="button"
                                onClick={() => void onDeleteSelected()}
                                disabled={bulkBusy !== null || selectionHasVersions}
                                title={selectionHasVersions ? "CI version files can't be deleted" : "Delete selected files (incl. converted caches)"}
                                className={`bg-fail hover:bg-fail active:bg-fail-subtle ${btn}`}
                            >
                                {bulkBusy === "delete" ? "Deleting…" : "Delete"}
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={clearSelection}
                            disabled={bulkBusy !== null}
                            className={`ml-auto bg-surface-3 hover:bg-surface-3 ${btn}`}
                        >
                            Cancel
                        </button>
                    </div>
                );
            })()}
            {opNote && (
                <div className="mb-2 flex items-center gap-2 text-xs text-accent">
                    <span
                        className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0"
                        aria-hidden="true"
                    />
                    <span className="truncate flex-1 min-w-0" role="status">{opNote}</span>
                </div>
            )}
            {uploadName && (
                <div className="mb-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                        <span className="truncate flex-1 min-w-0" title={uploadName}>
                            Uploading {uploadName}
                        </span>
                        <span className="shrink-0 tabular-nums">
                            {uploadTotal > 0
                                ? `${formatBytes(uploadLoaded)} / ${formatBytes(uploadTotal)}`
                                : formatBytes(uploadLoaded)}
                        </span>
                    </div>
                    <div className="mt-1 h-1 w-full bg-surface-2 rounded-sm overflow-hidden">
                        {uploadTotal > 0 ? (
                            <div
                                className="h-full bg-accent transition-[width] duration-200"
                                style={{
                                    width: `${Math.max(
                                        0,
                                        Math.min(100, Math.round((uploadLoaded / uploadTotal) * 100)),
                                    )}%`,
                                }}
                            />
                        ) : (
                            <div className="h-full w-1/3 bg-accent animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                        )}
                    </div>
                </div>
            )}
            {proceduralModels.length > 0 && (
                <div className="mb-1">
                    <div className="text-[10px] uppercase tracking-wide text-content-muted px-2">Procedural models</div>
                    {proceduralModels.map((m) => (
                        <div
                            key={m.id}
                            className={
                                "flex items-center gap-1.5 px-2 py-1 rounded-sm hover:bg-surface-2 cursor-pointer " +
                                (activeProcedural === m.id ? "bg-accent-subtle" : "")
                            }
                            onClick={() => void openProceduralModel(m)}
                            title="Procedural cell model (single database source) — click to open in the cellbuilder"
                        >
                            <ProceduralModelIcon className="shrink-0"/>
                            <span className="truncate text-sm">{m.name}</span>
                            <span className="text-[10px] text-info border border-info rounded-sm px-1">
                                r{m.revision}
                            </span>
                            <span className="ml-auto flex items-center gap-1">
                                {m.latest_glb_key && (
                                    <button
                                        className="px-1 rounded-sm hover:bg-surface-3"
                                        title="View compiled result"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            void useCellBuilderStore.getState().viewResult(m.latest_glb_key!);
                                        }}
                                    >
                                        <ViewIcon/>
                                    </button>
                                )}
                                <button
                                    className="px-1 rounded-sm hover:bg-surface-3"
                                    title="Delete procedural model"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        void deleteProceduralModel(m);
                                    }}
                                >
                                    🗑
                                </button>
                            </span>
                        </div>
                    ))}
                </div>
            )}
            {files.length === 0 && pendingFolders.length === 0 && newFolderAt === null ? (
                <div
                    className="text-xs italic text-content rounded-sm border border-dashed border-edge p-3"
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
                                "focus-visible:ring-1 focus-visible:ring-accent rounded-sm " +
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
                                        "mb-1 px-2 py-1 text-[11px] text-content rounded-sm " +
                                        "border border-dashed border-accent bg-accent-subtle"
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
                                    <FolderClosedIcon className="shrink-0 text-accent"/>
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
                                                onSelectToggle={(name, shiftKey) => {
                                                    setFocusedKey(`file:${name}`);
                                                    if (shiftKey && lastSelectedRef.current && lastSelectedRef.current !== name) {
                                                        // Range-select between the anchor and this
                                                        // row, in visible order.
                                                        const fileNames = flatRows
                                                            .filter((r) => r.kind === "file")
                                                            .map((r) => (r as {name: string}).name);
                                                        const a = fileNames.indexOf(lastSelectedRef.current);
                                                        const b = fileNames.indexOf(name);
                                                        if (a >= 0 && b >= 0) {
                                                            const [lo, hi] = a < b ? [a, b] : [b, a];
                                                            setSelection((prev) => {
                                                                const next = new Set(prev);
                                                                for (let i = lo; i <= hi; i++) next.add(fileNames[i]);
                                                                return next;
                                                            });
                                                            lastSelectedRef.current = name;
                                                            return;
                                                        }
                                                    }
                                                    lastSelectedRef.current = name;
                                                    toggleSelection(name);
                                                }}
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
                                                    <FolderClosedIcon className="shrink-0 text-accent"/>
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
                                    <ul className="flex flex-col divide-y divide-edge">
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
            {ctxMenu && (
                <PositionedMenu
                    items={ctxMenu.items}
                    header={ctxMenu.header}
                    onClose={() => setCtxMenu(null)}
                    anchor={{kind: "point", x: ctxMenu.x, y: ctxMenu.y}}
                />
            )}
            {pickerName && (
                <FieldPickerModal
                    sourceName={pickerName}
                    onClose={() => setPickerName(null)}
                />
            )}
            {/* FeaStreamingPickerModal retired — streaming sessions
                load with defaults via the toggle and refine via
                SimulationControls. */}
            {gitHistoryOpen && (
                <GitHistoryPanel
                    files={files}
                    loadedSourceNames={loadedSourceNames}
                    busyName={viewingName}
                    onToggle={onToggle}
                    onClose={() => setGitHistoryOpen(false)}
                />
            )}
            <FolderPickerModal
                open={picker !== null}
                title={picker?.title ?? ""}
                existingFolders={existingFolderPaths}
                allowRoot={picker?.allowRoot}
                submitLabel={picker?.submitLabel}
                onCancel={() => setPicker(null)}
                onPick={(folder) => {
                    const action = picker?.onPick;
                    setPicker(null);
                    if (action) void action(folder);
                }}
            />
        </div>
    );
};

// ──────────────────────────────────────────────────────────────────
// FileRow: one storage entry, optionally indented (for use inside
// the per-commit subtree). Pulled out of the main component so the
// versions tree can render the same row at indent 2 without
// re-implementing the toggle/expand/spinner machinery.
// ──────────────────────────────────────────────────────────────────


export default StorageBrowser;
