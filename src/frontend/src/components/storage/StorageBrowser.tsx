import {PANEL_CHROME} from "@/state/themeStore";
import React, {useEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {useConversionStore} from "@/state/conversionStore";
import {useModelState} from "@/state/modelState";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {runtime} from "@/runtime/config";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {load_fea_with_defaults} from "@/utils/scene/handlers/load_fea_streaming";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import ReloadIcon from "../icons/ReloadIcon";
import PlusIcon from "../icons/PlusIcon";
import ExpandIcon from "../icons/ExpandIcon";
import FileTypeIcon from "../icons/FileTypeIcon";
import ViewIcon from "../icons/ViewIcon";
import FolderClosedIcon from "../icons/FolderClosedIcon";
import FolderOpenIcon from "../icons/FolderOpenIcon";
import ChevronRightIcon from "../icons/ChevronRightIcon";
import FieldPickerModal from "./FieldPickerModal";
import GitHistoryPanel from "./GitHistoryPanel";
import {BuildSidecar, useBuildSidecars} from "@/hooks/useBuildSidecars";
import {
    buildFileTree,
    collectFolderPaths,
    FileTreeNode,
    FolderNode,
    loadExpandedFolders,
    loadPendingFolders,
    saveExpandedFolders,
    savePendingFolders,
} from "@/utils/storage/fileTree";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import PositionedMenu, {KebabMenuItem} from "@/components/common/PositionedMenu";
import FolderPickerModal from "@/components/common/FolderPickerModal";
import {viewerApi} from "@/services/viewerApi";
import {useStorageMutations} from "./useStorageMutations";
import {buildFileMenuItems, buildFolderMenuItems} from "./storageMenuItems";
import {canLoadIntoSceneLegacy, isFEAResult, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";

// Custom drag MIME for in-panel file moves. OS-file drops arrive as
// ``dataTransfer.files`` instead; checking for this type tells the two
// apart (types are readable during dragover, the payload only on drop).
const KEYS_MIME = "application/x-adapy-keys";

// Small inline CSS spinner. Uses border tricks rather than an SVG so
// it scales with text size and stays crisp at 16px tall icons.
const Spinner: React.FC<{className?: string}> = ({className = ""}) => (
    <span
        className={`inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin ${className}`}
        aria-hidden="true"
    />
);

function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function dirnameOf(key: string): string {
    const i = key.lastIndexOf("/");
    return i >= 0 ? key.slice(0, i) : "";
}

function basenameOf(key: string): string {
    return key.split("/").pop() ?? key;
}

// CI uploads land at ``versions/<branch>/<commit>/<filename>``; the
// helpers below split the storage list into "regular" files (treated
// as before) and a tree grouped by branch + commit so the storage
// browser can show a collapsible per-branch history with the latest
// commit pinned.

interface VersionLeaf {
    file: ServerFileEntry;
    artefactName: string;       // basename — last segment of the key
}

interface CommitGroup {
    sha: string;                // <commit> path segment (full SHA, usually 40 chars)
    leaves: VersionLeaf[];
    /** Sort key. Prefers ``git.timestamp`` from the build.json sidecar;
     *  falls back to S3 ``lastModified`` until the sidecar resolves.
     *  Mtime is wrong for "latest" because re-running CI on an older
     *  commit refreshes the mtime — the git timestamp is what actually
     *  reflects commit order. */
    sortKey: number;            // ms since epoch
    /** True when ``sortKey`` came from the sidecar (authoritative). */
    sortFromSidecar: boolean;
}

interface BranchGroup {
    encodedBranch: string;      // path-safe form (slashes replaced with __)
    displayBranch: string;      // human-friendly (slashes restored)
    commits: CommitGroup[];     // sorted newest-first by sortKey
    sortKey: number;            // max across commits
}

function parseLastModifiedMs(iso: string): number {
    if (!iso) return 0;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : 0;
}

function classifyFiles(
    files: ServerFileEntry[],
    sidecars: ReadonlyMap<string, BuildSidecar | null>,
): {
    regular: ServerFileEntry[];
    branches: BranchGroup[];
} {
    const regular: ServerFileEntry[] = [];
    // branch → sha → leaves
    const tree = new Map<string, Map<string, VersionLeaf[]>>();
    for (const f of files) {
        const trimmed = f.name.replace(/^\/+/, "");
        const parts = trimmed.split("/");
        if (parts.length >= 4 && parts[0] === "versions") {
            const [, encodedBranch, sha, ...rest] = parts;
            const artefactName = rest.join("/");
            // Hide the .build.json sidecars from the visible tree —
            // they're metadata for the GLB artefact, not separately
            // user-loadable. Clicking the GLB row will load the GLB;
            // the sidecar comes along under the same prefix when we
            // need it (e.g. for git-history view).
            if (artefactName.endsWith(".build.json")) continue;
            let perBranch = tree.get(encodedBranch);
            if (!perBranch) {
                perBranch = new Map();
                tree.set(encodedBranch, perBranch);
            }
            let leaves = perBranch.get(sha);
            if (!leaves) {
                leaves = [];
                perBranch.set(sha, leaves);
            }
            leaves.push({file: f, artefactName});
        } else {
            regular.push(f);
        }
    }

    const branches: BranchGroup[] = [];
    for (const [encodedBranch, perBranchMap] of tree) {
        const commits: CommitGroup[] = [];
        for (const [sha, leaves] of perBranchMap) {
            const sidecar = sidecars.get(`${encodedBranch}/${sha}`);
            const sidecarTs = sidecar?.git.timestamp
                ? parseLastModifiedMs(sidecar.git.timestamp)
                : 0;
            const mtime = leaves.reduce(
                (acc, l) => Math.max(acc, parseLastModifiedMs(l.file.lastModified)),
                0,
            );
            const sortFromSidecar = sidecarTs > 0;
            commits.push({
                sha,
                leaves,
                sortKey: sortFromSidecar ? sidecarTs : mtime,
                sortFromSidecar,
            });
        }
        commits.sort((a, b) => b.sortKey - a.sortKey);
        const branchLatest = commits.length > 0 ? commits[0].sortKey : 0;
        branches.push({
            encodedBranch,
            displayBranch: encodedBranch.replace(/__/g, "/"),
            commits,
            sortKey: branchLatest,
        });
    }
    branches.sort((a, b) => b.sortKey - a.sortKey);
    return {regular, branches};
}

function shortSha(sha: string): string {
    return sha.length > 8 ? sha.slice(0, 8) : sha;
}

// File-tree shape comes from ``@/utils/storage/fileTree``; here we
// just specialise the generic to ``ServerFileEntry`` so existing call
// sites read the same as before. The admin StorageTab uses the same
// helpers with its own entry type.
type ServerFileTreeNode = FileTreeNode<ServerFileEntry>;
type ServerFolderNode = FolderNode<ServerFileEntry>;

function countFiles(node: ServerFileTreeNode): number {
    if (node.kind === "file") return 1;
    return node.children.reduce((acc, c) => acc + countFiles(c), 0);
}

function formatRelative(iso: string): string {
    const t = parseLastModifiedMs(iso);
    if (t === 0) return "";
    const dt = (Date.now() - t) / 1000;
    if (dt < 60) return "just now";
    if (dt < 3600) return `${Math.round(dt / 60)} min ago`;
    if (dt < 86400) return `${Math.round(dt / 3600)} h ago`;
    if (dt < 7 * 86400) return `${Math.round(dt / 86400)} d ago`;
    return new Date(t).toISOString().slice(0, 10);
}

// Inline name editor used for file/folder rename and new-folder
// creation. Enter commits, Escape (or blur) cancels. ``selectStem``
// pre-selects the basename-without-extension so a quick type replaces
// the name but keeps the extension.
const InlineNameInput: React.FC<{
    initial: string;
    placeholder?: string;
    selectStem?: boolean;
    onCommit: (value: string) => void;
    onCancel: () => void;
}> = ({initial, placeholder, selectStem, onCommit, onCancel}) => {
    const inputRef = useRef<HTMLInputElement>(null);
    useEffect(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        if (selectStem) {
            const dot = initial.lastIndexOf(".");
            el.setSelectionRange(0, dot > 0 ? dot : initial.length);
        } else {
            el.select();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
        <input
            ref={inputRef}
            type="text"
            defaultValue={initial}
            placeholder={placeholder}
            className={
                "flex-1 min-w-0 bg-gray-800 border border-blue-500 rounded-sm " +
                "px-1 py-0.5 text-xs text-gray-100 focus:outline-hidden"
            }
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
                if (e.key === "Enter") {
                    onCommit((e.target as HTMLInputElement).value);
                } else if (e.key === "Escape") {
                    onCancel();
                }
            }}
            onBlur={onCancel}
        />
    );
};

const StorageBrowser: React.FC = () => {
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
    // Upload progress: name = current file (or null), loaded/total in
    // bytes. Total may stay 0 if the browser can't determine it (rare
    // for File uploads); we treat that as indeterminate.
    const [uploadName, setUploadName] = useState<string | null>(null);
    const [uploadLoaded, setUploadLoaded] = useState(0);
    const [uploadTotal, setUploadTotal] = useState(0);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    const [viewingName, setViewingName] = useState<string | null>(null);
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
    const [ctxMenu, setCtxMenu] = useState<{x: number; y: number; items: KebabMenuItem[]} | null>(null);
    const openCtxMenu = (e: React.MouseEvent, items: KebabMenuItem[]) => {
        if (items.length === 0) return;
        e.preventDefault();
        e.stopPropagation();
        setCtxMenu({x: e.clientX, y: e.clientY, items});
    };
    // In-panel drag state: keys being dragged (for row dimming + the
    // move-to-root strip). Cleared on dragend/drop.
    const [draggingKeys, setDraggingKeys] = useState<string[] | null>(null);
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

    // The picker modal drives both move flows; ``onPick`` is the
    // closure that knows what to do once a destination is chosen.
    const [picker, setPicker] = useState<{
        title: string;
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

    const onMoveSingleToFolder = (key: string) => {
        setPicker({
            title: `Move "${key}" to folder`,
            onPick: async (folder) => {
                try {
                    const r = await mutations.moveKeys([key], folder);
                    if (r.failed.length > 0) {
                        window.alert(r.failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
                    }
                    void request_list_of_files_from_server();
                } catch (e) {
                    alertError(e);
                }
            },
        });
    };

    const runFolderMove = async (folderPath: string, newPath: string) => {
        if (newPath === folderPath) return;
        try {
            const allKeys = files.map((f) => f.name);
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
        if (!window.confirm(
            `Delete folder "${path}" and its ${fileCount} file${fileCount === 1 ? "" : "s"}?\n` +
            "Converted view caches are removed too.",
        )) return;
        const prefix = path + "/";
        const targets = files.filter((x) => x.name.replace(/^\/+/, "").startsWith(prefix));
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
        if (viewingName) return; // already busy with another file
        setViewingName(entry.name);
        try {
            if (nextChecked) {
                if (isStreamingFEAResult(entry.name)) {
                    // Streaming-FEA toggle: fetch manifest, load with
                    // defaults (first field, default reduction, step 0),
                    // and let SimulationControls take it from there.
                    // No more picker modal in the way.
                    await load_fea_with_defaults(entry.name);
                } else {
                    await overlay_file_in_scene(entry.name);
                }
            } else {
                if (isStreamingFEAResult(entry.name)) {
                    // FEA streaming meshes were loaded via replace_model
                    // (scene-wide replace, no per-source group registered).
                    // unload_source_from_scene would no-op because there's
                    // no group ref to look up. Clear the whole scene
                    // instead — that also tears down the FEA session
                    // store + animation driver.
                    await clear_loaded_model();
                } else {
                    unload_source_from_scene(entry.name);
                }
            }
        } catch (err) {
            console.error("storage toggle failed", err);
        } finally {
            setViewingName(null);
        }
    };

    // Load a STEP file via the memory-bounded streaming converter (one solid at a
    // time) — for large assemblies whose normal OCC->GLB conversion OOM-kills the
    // worker. Same overlay flow as onToggle, with the streamer flag set.
    const onLoadStreamer = async (name: string) => {
        if (viewingName) return;
        setViewingName(name);
        try {
            await overlay_file_in_scene(name, undefined, {streamer: true});
        } catch (err) {
            console.error("streamer load failed", err);
        } finally {
            setViewingName(null);
        }
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
    const onLoadSelected = async () => {
        if (bulkBusy !== null) return;
        const targets = files.filter((f) =>
            selection.has(f.name) && !loadedSourceNames.has(f.name) &&
            (isStreamingFEAResult(f.name) || canLoadIntoSceneLegacy(f.name)));
        if (targets.length === 0) {
            clearSelection();
            return;
        }
        setBulkBusy("load");
        try {
            for (const f of targets) {
                setViewingName(f.name);
                try {
                    await overlay_file_in_scene(f.name);
                } catch (err) {
                    console.error("load-selected overlay failed", f.name, err);
                }
            }
        } finally {
            setViewingName(null);
            setBulkBusy(null);
            clearSelection();
        }
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
            "Converted view caches are removed too.",
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
            onPick: async (folder) => {
                try {
                    const r = await mutations.moveKeys(keys, folder);
                    if (r.failed.length > 0) {
                        window.alert(r.failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
                    }
                    clearSelection();
                    void request_list_of_files_from_server();
                } catch (e) {
                    alertError(e);
                }
            },
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
        const folder = uploadTargetRef.current ?? undefined;
        uploadTargetRef.current = null;
        void uploadFilesTo(picked, folder);
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
            if (keys.length === 0) return;
            try {
                if (target === "") {
                    // Move-to-root: the move endpoint requires a non-empty
                    // folder, so root moves are per-key renames to the
                    // basename.
                    for (const k of keys) {
                        await mutations.renameKey(k, basenameOf(k));
                    }
                } else {
                    const r = await mutations.moveKeys(keys, target);
                    if (r.failed.length > 0) {
                        window.alert(r.failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
                    }
                }
                clearSelection();
                void request_list_of_files_from_server();
            } catch (err) {
                alertError(err);
            }
            return;
        }
        if (e.dataTransfer.files?.length) {
            void uploadFilesTo(Array.from(e.dataTransfer.files), target || undefined);
        }
    };

    // ── Menu item builders (kebab + context menu share these) ───────
    const fileMenuItems = (f: ServerFileEntry, displayName: string): KebabMenuItem[] => {
        const busy = viewingName !== null;
        return buildFileMenuItems(f, {
            isLoaded: loadedSourceNames.has(f.name),
            busy,
            loadDisabled: !isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name),
            canMutate,
            onToggle: (next) => void onToggle(f, next),
            onLoadStreamer:
                runtime.isRestMode() && runtime.convertEnabled()
                    ? () => void onLoadStreamer(f.name)
                    : undefined,
            onDownload: runtime.isRestMode() ? () => onDownloadFile(f.name) : undefined,
            onRename: () => setRenaming({kind: "file", path: f.name}),
            onMoveToFolder: () => onMoveSingleToFolder(f.name),
            onDelete: () => void onDeleteFile(f),
        });
    };
    // CI version blobs stay read-only: load/streamer/download only.
    const versionFileMenuItems = (f: ServerFileEntry): KebabMenuItem[] => {
        const busy = viewingName !== null;
        return buildFileMenuItems(f, {
            isLoaded: loadedSourceNames.has(f.name),
            busy,
            loadDisabled: !isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name),
            canMutate: false,
            onToggle: (next) => void onToggle(f, next),
            onLoadStreamer:
                runtime.isRestMode() && runtime.convertEnabled()
                    ? () => void onLoadStreamer(f.name)
                    : undefined,
            onDownload: runtime.isRestMode() ? () => onDownloadFile(f.name) : undefined,
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

    const showRootDropStrip =
        draggingKeys !== null && draggingKeys.some((k) => dirnameOf(k) !== "");

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
                PANEL_CHROME + " " +
                (maximized
                    ? "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[61] " +
                      "w-[calc(100vw-3rem)] h-[calc(100vh-3rem)] flex flex-col"
                    : "w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md")
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
                    <h2 className="font-bold truncate">Storage</h2>
                    {/* Show the active scope so it's clear which space
                        this list reflects. Files uploaded under one
                        scope are invisible to a list query under another
                        — surfacing the name avoids the "I uploaded but
                        nothing shows" confusion when scope drifts. */}
                    <div className="text-[10px] uppercase tracking-wide text-gray-400 truncate"
                         title={currentScope?.kind ? `${currentScope.kind}${currentScope.id ? ":" + currentScope.id : ""}` : "shared"}>
                        scope: {currentScope?.name ?? "Shared"}
                    </div>
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
                    <button
                        ref={plusBtnRef}
                        type="button"
                        className={
                            "bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center disabled:opacity-60 " +
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-blue-400"
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
                            ]}
                            onClose={() => setPlusOpen(false)}
                            ignoreOutsideRef={plusBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    <button
                        type="button"
                        className={
                            "bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center " +
                            // 40px+ tap target on mobile per WCAG; tighter
                            // on desktop where the cursor is precise.
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-blue-400"
                        }
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
                    {anyLoaded && (
                        <button
                            type="button"
                            className={
                                "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-60 cursor-pointer " +
                                "text-white rounded-sm text-xs whitespace-nowrap " +
                                "px-2 sm:px-2 py-1 min-h-[40px] sm:min-h-0"
                            }
                            onClick={() => void onHideAll()}
                            disabled={bulkBusy !== null}
                            title="Unload every model currently in the scene"
                            aria-label="Clear scene"
                            aria-busy={bulkBusy === "clear"}
                        >
                            {bulkBusy === "clear" ? "Clearing…" : "Clear"}
                        </button>
                    )}
                    <button
                        type="button"
                        className={
                            "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center " +
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-blue-400"
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
                    <div className="mb-2 px-2 py-1.5 rounded-sm border border-gray-700 bg-gray-800/95 flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-white whitespace-nowrap">
                            {selection.size} selected
                        </span>
                        <button
                            type="button"
                            onClick={() => void onLoadSelected()}
                            disabled={bulkBusy !== null}
                            className={`bg-blue-700 hover:bg-blue-600 active:bg-blue-800 ${btn}`}
                        >
                            {bulkBusy === "load" ? "Loading…" : "Load"}
                        </button>
                        <button
                            type="button"
                            onClick={onUnloadSelected}
                            disabled={bulkBusy !== null}
                            className={`bg-gray-700 hover:bg-gray-600 active:bg-gray-800 ${btn}`}
                        >
                            {bulkBusy === "unload" ? "Unloading…" : "Unload"}
                        </button>
                        {canMutate && (
                            <button
                                type="button"
                                onClick={onMoveSelected}
                                disabled={bulkBusy !== null || selectionHasVersions}
                                title={selectionHasVersions ? "CI version files can't be moved" : "Move selected files to a folder"}
                                className={`bg-gray-700 hover:bg-gray-600 active:bg-gray-800 ${btn}`}
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
                                className={`bg-red-800 hover:bg-red-700 active:bg-red-900 ${btn}`}
                            >
                                {bulkBusy === "delete" ? "Deleting…" : "Delete"}
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={clearSelection}
                            disabled={bulkBusy !== null}
                            className={`ml-auto bg-gray-600 hover:bg-gray-500 ${btn}`}
                        >
                            Cancel
                        </button>
                    </div>
                );
            })()}
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
                    <div className="mt-1 h-1 w-full bg-gray-700 rounded-sm overflow-hidden">
                        {uploadTotal > 0 ? (
                            <div
                                className="h-full bg-blue-600 transition-[width] duration-200"
                                style={{
                                    width: `${Math.max(
                                        0,
                                        Math.min(100, Math.round((uploadLoaded / uploadTotal) * 100)),
                                    )}%`,
                                }}
                            />
                        ) : (
                            <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                        )}
                    </div>
                </div>
            )}
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
                    const {regular, branches} = classifyFiles(files, sidecars);
                    return (
                        <div
                            className={
                                "flex flex-col overflow-auto " +
                                (maximized ? "flex-1 min-h-0" : "max-h-80")
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
                                const tree = buildFileTree(regular, (f) => f.name, pendingFolders);
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
                                                selectionMode={inSelectionMode}
                                                isSelected={selection.has(node.file.name)}
                                                onLongPress={toggleSelection}
                                                onSelectToggle={toggleSelection}
                                                menuItems={items}
                                                onOpenContextMenu={(e) => openCtxMenu(e, items)}
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
                                                onToggle={() => toggleFolder(node.path)}
                                                menuItems={items}
                                                onOpenContextMenu={(e) => openCtxMenu(e, items)}
                                                onDropInto={(e) => void handleDropOnFolder(node.path, e)}
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
                                    selectionMode={inSelectionMode}
                                    selection={selection}
                                    onLongPress={toggleSelection}
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

interface FolderRowProps {
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
    renaming?: boolean;
    onRenameCommit?: (newName: string) => void;
    onRenameCancel?: () => void;
}

const FolderRow: React.FC<FolderRowProps> = ({
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
    renaming,
    onRenameCommit,
    onRenameCancel,
}) => {
    const indentPx = depth * 12;
    // dragenter/dragleave fire per child element; a counter survives
    // the churn where a plain boolean would flicker.
    const [dragHover, setDragHover] = useState(0);
    const acceptsDrop = (e: React.DragEvent) =>
        e.dataTransfer.types.includes(KEYS_MIME) || e.dataTransfer.types.includes("Files");
    return (
        <li
            className={
                "flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer select-none " +
                "hover:bg-gray-800/80 " +
                (dragHover > 0 ? "ring-1 ring-blue-400 bg-blue-900/30 " : "") +
                (isPending ? "opacity-80 " : "")
            }
            style={{paddingLeft: 8 + indentPx}}
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
                    "shrink-0 text-blue-400 transition-transform duration-150 " +
                    (expanded ? "rotate-90" : "")
                }
            />
            {/* Folder glyph swaps closed↔open with the expand state.
                Same blue tone so eye + chevron read as one
                composite control. */}
            {expanded ? (
                <FolderOpenIcon className="shrink-0 text-blue-400"/>
            ) : (
                <FolderClosedIcon className="shrink-0 text-blue-400"/>
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
                    className="shrink-0 inline-flex items-center gap-0.5 text-blue-400"
                    title={`${loadedCount} loaded model${loadedCount === 1 ? "" : "s"} inside`}
                >
                    <ViewIcon width="14px" height="14px"/>
                    {(loadedCount ?? 0) > 1 && (
                        <span className="text-[10px] tabular-nums">{loadedCount}</span>
                    )}
                </span>
            )}
            <span className="text-[10px] text-gray-400 shrink-0">
                {isPending ? "empty" : fileCount}
            </span>
            {menuItems.length > 0 && (
                <span
                    className="shrink-0"
                    onClick={(e) => e.stopPropagation()}
                >
                    <RowKebabMenu
                        ariaLabel={`Organize folder ${folder.path}`}
                        buttonClassName="h-6 w-6 text-gray-300 hover:bg-gray-700"
                        header={<span className="font-mono">{folder.path}/</span>}
                        items={menuItems}
                    />
                </span>
            )}
        </li>
    );
};

interface FileRowProps {
    file: ServerFileEntry;
    displayName: string;
    indentLevel: number;
    viewingName: string | null;
    loadedSourceNames: ReadonlySet<string>;
    conversionJobs: Record<string, {progress: number; status?: string}>;
    expandedName: string | null;
    setExpandedName: (n: string | null) => void;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    setPickerName: (n: string | null) => void;
    selectionMode: boolean;
    isSelected: boolean;
    onLongPress: (name: string) => void;
    onSelectToggle: (name: string) => void;
    /** Row actions — shared between the kebab and the right-click
     * context menu (parent builds both from one list). */
    menuItems: KebabMenuItem[];
    onOpenContextMenu?: (e: React.MouseEvent) => void;
    /** In-panel drag source (move to folder). */
    draggable?: boolean;
    onDragStartRow?: (e: React.DragEvent) => void;
    onDragEndRow?: () => void;
    /** OS-file drops on this row (upload into the row's folder). */
    onDropAt?: (e: React.DragEvent) => void;
    /** Row is part of the in-flight drag payload. */
    dimmed?: boolean;
    renaming?: boolean;
    onRenameCommit?: (newBasename: string) => void;
    onRenameCancel?: () => void;
    /** Maximized view: show the last-modified column. */
    showModified?: boolean;
}

const FileRow: React.FC<FileRowProps> = ({
    file: f,
    displayName,
    indentLevel,
    viewingName,
    loadedSourceNames,
    conversionJobs,
    expandedName,
    setExpandedName,
    onToggle,
    setPickerName,
    selectionMode,
    isSelected,
    onLongPress,
    onSelectToggle,
    menuItems,
    onOpenContextMenu,
    draggable,
    onDragStartRow,
    onDragEndRow,
    onDropAt,
    dimmed,
    renaming,
    onRenameCommit,
    onRenameCancel,
    showModified,
}) => {
    const isViewing = viewingName === f.name;
    const otherViewing = viewingName !== null && !isViewing;
    const isLoaded = loadedSourceNames.has(f.name);
    const viewJob = isViewing ? conversionJobs[`${f.name}::glb`] : undefined;
    const viewProgressPct = viewJob
        ? Math.max(0, Math.min(100, Math.round(viewJob.progress * 100)))
        : 0;
    const indentPx = indentLevel * 12;

    // Long-press to enter selection mode. 500 ms hold, cancelled by
    // pointer move > 8 px (treats it as a scroll, not a hold). Once
    // we're in selection mode, taps toggle membership instead — see
    // the row's onClick below.
    const longPressTimer = useRef<number | null>(null);
    const longPressStart = useRef<{x: number; y: number} | null>(null);
    const longPressFired = useRef(false);
    const cancelLongPress = () => {
        if (longPressTimer.current !== null) {
            window.clearTimeout(longPressTimer.current);
            longPressTimer.current = null;
        }
    };
    const onPointerDown: React.PointerEventHandler = (e) => {
        if (selectionMode) return; // already in mode; rely on click
        longPressStart.current = {x: e.clientX, y: e.clientY};
        longPressFired.current = false;
        cancelLongPress();
        longPressTimer.current = window.setTimeout(() => {
            longPressFired.current = true;
            onLongPress(f.name);
        }, 500);
    };
    const onPointerMove: React.PointerEventHandler = (e) => {
        if (!longPressStart.current) return;
        const dx = e.clientX - longPressStart.current.x;
        const dy = e.clientY - longPressStart.current.y;
        if (dx * dx + dy * dy > 64) cancelLongPress();
    };
    const onPointerUp: React.PointerEventHandler = () => {
        cancelLongPress();
        longPressStart.current = null;
    };
    useEffect(() => () => cancelLongPress(), []);

    return (
        <li
            className={
                "flex flex-col pr-1 py-1 text-xs rounded " +
                (dimmed ? "opacity-40 " : "") +
                (isSelected ? "bg-amber-700/30 " : "hover:bg-gray-800/60 ") +
                (selectionMode ? "cursor-pointer" : "")
            }
            style={{paddingLeft: `${8 + indentPx}px`}}
            draggable={draggable || undefined}
            onDragStart={draggable && onDragStartRow ? (e) => {
                cancelLongPress();
                onDragStartRow(e);
            } : undefined}
            onDragEnd={onDragEndRow}
            onDragOver={onDropAt ? (e) => e.preventDefault() : undefined}
            onDrop={onDropAt ? (e) => {
                e.preventDefault();
                e.stopPropagation();
                onDropAt(e);
            } : undefined}
            onContextMenu={onOpenContextMenu}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={() => {
                cancelLongPress();
                longPressStart.current = null;
            }}
            onClick={(e) => {
                if (longPressFired.current) {
                    // The long-press already toggled selection on; the
                    // synthetic click fires after pointerup and would
                    // double-toggle if we let it through.
                    longPressFired.current = false;
                    e.stopPropagation();
                    return;
                }
                if (selectionMode) {
                    onSelectToggle(f.name);
                    return;
                }
                // Tap/click anywhere on the row (outside the toggle and
                // row buttons) opens the same menu as right-click — the
                // primary mobile path to rename/move/delete/download.
                onOpenContextMenu?.(e);
            }}
        >
            <div className="flex items-center justify-between gap-2">
                {/* Normal mode: the checkbox IS the load toggle —
                    checked (+ the eye marker) while the model is in the
                    scene; clicking it loads/unloads directly. Long-press
                    swaps the rows into selection mode, where the amber
                    circle marks bulk-toolbar membership instead. */}
                {selectionMode ? (
                    <span
                        className={
                            "h-5 w-5 shrink-0 rounded-full border-2 inline-flex items-center justify-center " +
                            (isSelected
                                ? "bg-amber-600 border-amber-400"
                                : "border-gray-400")
                        }
                        aria-checked={isSelected}
                        role="checkbox"
                    >
                        {isSelected && (
                            <svg viewBox="0 0 16 16" className="w-3 h-3 fill-white" aria-hidden>
                                <path d="M6 11.2 2.4 7.6l1.4-1.4L6 8.4l6.2-6.2 1.4 1.4z"/>
                            </svg>
                        )}
                    </span>
                ) : (
                    <input
                        type="checkbox"
                        className="h-5 w-5 shrink-0 cursor-pointer disabled:cursor-not-allowed"
                        checked={isLoaded}
                        onChange={() => void onToggle(f, !isLoaded)}
                        onClick={(e) => e.stopPropagation()}
                        disabled={
                            isViewing || otherViewing ||
                            (!isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name))
                        }
                        aria-busy={isViewing || undefined}
                        title={isLoaded
                            ? "Unload from scene"
                            : isStreamingFEAResult(f.name)
                                ? "Open in streaming FEA viewer"
                                : "Load into scene"}
                    />
                )}
                <FileTypeIcon name={f.name}/>
                {renaming && onRenameCommit && onRenameCancel ? (
                    <InlineNameInput
                        initial={displayName}
                        selectStem
                        onCommit={onRenameCommit}
                        onCancel={onRenameCancel}
                    />
                ) : (
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            if (selectionMode) {
                                onSelectToggle(f.name);
                                return;
                            }
                            // Same menu as right-click — full path shows
                            // in the menu header, so no separate
                            // truncation toggle is needed.
                            onOpenContextMenu?.(e);
                        }}
                        className={`flex-1 min-w-0 text-left ${expandedName === f.name ? 'whitespace-normal break-all' : 'truncate'} ${isLoaded ? 'text-blue-200 font-medium' : ''}`}
                        title={f.name}
                    >
                        {displayName}
                    </button>
                )}
                <div className="flex items-center gap-1 shrink-0">
                    {showModified && (
                        <span
                            className="text-[10px] text-gray-400 tabular-nums whitespace-nowrap"
                            title={f.lastModified}
                        >
                            {formatRelative(f.lastModified)}
                        </span>
                    )}
                    {/* Explicit "in scene" marker. The checkbox is a
                        selection control (bulk actions), so loaded
                        state needs its own signal — the blue filename
                        tint alone is easy to miss on mobile. */}
                    {isLoaded && !isViewing && (
                        <ViewIcon
                            width="16px"
                            height="16px"
                            className="text-blue-400"
                            aria-label="Loaded in scene"
                        />
                    )}
                    {isViewing && <Spinner/>}
                    {/* Legacy single-shot (step, field) picker — kept
                        only for hypothetical future non-streaming FEA
                        formats. SIF goes through the streaming bake
                        now (toggle the checkbox; refine field /
                        reduction / step in SimulationControls), so
                        the picker entry point would just confuse the
                        user with two parallel ways to load the same
                        file. Gated on ``!isStreamingFEAResult`` so
                        the moment a new isFEAResult format that is
                        NOT in the streaming set ships, the picker
                        re-appears for it without code changes here. */}
                    {isFEAResult(f.name) && !isStreamingFEAResult(f.name) && runtime.isRestMode() && runtime.convertEnabled() && (
                        <button
                            className="p-1 rounded-sm text-white hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
                            onClick={(e) => {
                                e.stopPropagation();
                                setPickerName(f.name);
                            }}
                            disabled={otherViewing || isViewing}
                            title="Pick step / field"
                            aria-label="Pick step / field"
                        >
                            <span className="leading-none text-sm font-mono">⇅</span>
                        </button>
                    )}
                    {menuItems.length > 0 && (
                        <span onClick={(e) => e.stopPropagation()}>
                            <RowKebabMenu
                                ariaLabel={`Actions for ${displayName}`}
                                buttonClassName="h-7 w-7 text-gray-200 hover:bg-gray-700"
                                header={<span className="font-mono" title={f.name}>{f.name}</span>}
                                items={menuItems}
                            />
                        </span>
                    )}
                </div>
            </div>
            {isViewing && (
                <div className="mt-1 h-1 w-full bg-gray-700 rounded-sm overflow-hidden">
                    {viewJob && viewJob.status !== 'queued' ? (
                        <div
                            className="h-full bg-blue-600 transition-[width] duration-200"
                            style={{width: `${viewProgressPct}%`}}
                        />
                    ) : (
                        <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                    )}
                </div>
            )}
        </li>
    );
};

// ──────────────────────────────────────────────────────────────────
// VersionsTree: renders the CI-uploaded ``versions/<branch>/<sha>/…``
// blobs as a 3-level collapsible list:
//
//   Versions
//   ├ <branch>                   ← collapsible. Sorted newest-tip first.
//   │  ├ <commit (relative t)>   ← Latest of branch is auto-expanded
//   │  │  ├ welds_model.glb      ← <FileRow indentLevel=2/>
//   │  │  └ welds_model.ifc
//   │  └ <older commit>          ← collapsed by default
//   └ <other branch>
//
// All branches collapse-by-default except the most recently active
// one, whose latest commit is also auto-expanded. State is local to
// the panel; refresh resets it.
//
// Version blobs are CI build outputs — read-only by design. Their
// rows get load/download menus only: no rename/move/delete, no drag,
// no drop targets.
// ──────────────────────────────────────────────────────────────────

interface VersionsTreeProps {
    branches: BranchGroup[];
    sidecars: ReadonlyMap<string, BuildSidecar | null>;
    viewingName: string | null;
    loadedSourceNames: ReadonlySet<string>;
    conversionJobs: Record<string, {progress: number; status?: string}>;
    expandedName: string | null;
    setExpandedName: (n: string | null) => void;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    setPickerName: (n: string | null) => void;
    onOpenGitHistory: () => void;
    selectionMode: boolean;
    selection: Set<string>;
    onLongPress: (name: string) => void;
    onSelectToggle: (name: string) => void;
    /** Read-only menu builder from the parent (load/streamer/download). */
    fileMenuItemsFor: (file: ServerFileEntry) => KebabMenuItem[];
    onOpenContextMenu: (e: React.MouseEvent, items: KebabMenuItem[]) => void;
    showModified?: boolean;
}

const VersionsTree: React.FC<VersionsTreeProps> = (props) => {
    const {branches} = props;
    // Auto-expand: the freshest branch + its freshest commit.
    //
    // Why an effect instead of just useState's lazy initializer: on the
    // first render sidecars haven't loaded yet, so classifyFiles sorts
    // by S3 mtime and ``branches[0].commits[0]`` is the mtime-freshest
    // commit, not the git-freshest one. Once sidecars arrive the
    // sort flips and the "latest" pill moves — but a snapshot taken
    // at construction time would leave the *wrong* commit auto-
    // expanded, with the GLB-toggle row sitting under the previous
    // mtime-freshest commit. Re-sync until the user has interacted;
    // freeze after any manual toggle so we don't yank an opened
    // panel shut on the next sidecar update.
    const freshestBranch = branches.length > 0 ? branches[0].encodedBranch : null;
    const freshestKey =
        branches.length > 0 && branches[0].commits.length > 0
            ? `${freshestBranch}/${branches[0].commits[0].sha}`
            : null;
    const userTouchedRef = useRef(false);
    const [openBranches, setOpenBranches] = useState<Set<string>>(
        () => new Set(freshestBranch ? [freshestBranch] : []),
    );
    const [openCommits, setOpenCommits] = useState<Set<string>>(
        () => new Set(freshestKey ? [freshestKey] : []),
    );

    useEffect(() => {
        if (userTouchedRef.current) return;
        if (freshestBranch === null) return;
        setOpenBranches(new Set([freshestBranch]));
        setOpenCommits(freshestKey ? new Set([freshestKey]) : new Set());
    }, [freshestBranch, freshestKey]);

    const toggleBranch = (b: string) => {
        userTouchedRef.current = true;
        setOpenBranches((prev) => {
            const next = new Set(prev);
            if (next.has(b)) next.delete(b);
            else next.add(b);
            return next;
        });
    };
    const toggleCommit = (key: string) => {
        userTouchedRef.current = true;
        setOpenCommits((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    };

    return (
        <div className="border-t border-gray-700/60 pt-1 mt-1">
            <div className="flex items-center justify-between px-1 pb-1">
                <div className="text-[10px] uppercase tracking-wide text-gray-400">
                    Versions
                </div>
                <button
                    type="button"
                    onClick={props.onOpenGitHistory}
                    className="text-[10px] px-1.5 py-0.5 rounded-sm bg-gray-700 hover:bg-gray-600 text-white"
                    title="Open chronological commit timeline with author + parent links"
                >
                    Git history
                </button>
            </div>
            <ul className="flex flex-col divide-y divide-gray-700/40">
                {branches.map((b, bIdx) => {
                    const branchOpen = openBranches.has(b.encodedBranch);
                    return (
                        <li key={b.encodedBranch} className="flex flex-col">
                            <button
                                type="button"
                                onClick={() => toggleBranch(b.encodedBranch)}
                                className="flex items-center gap-1 px-1 py-1 text-xs text-left w-full hover:bg-gray-800/80"
                                aria-expanded={branchOpen}
                                title={b.displayBranch}
                            >
                                <span className="w-3 inline-block text-gray-300">
                                    {branchOpen ? "▾" : "▸"}
                                </span>
                                <span className="font-mono text-[11px] truncate flex-1 min-w-0">
                                    {b.displayBranch}
                                </span>
                                <span className="text-[10px] text-gray-400 shrink-0">
                                    {b.commits.length} commit{b.commits.length === 1 ? "" : "s"}
                                </span>
                            </button>
                            {branchOpen && (
                                <ul className="flex flex-col">
                                    {b.commits.map((c, cIdx) => {
                                        const commitKey = `${b.encodedBranch}/${c.sha}`;
                                        const commitOpen = openCommits.has(commitKey);
                                        const isLatest = bIdx === 0 && cIdx === 0;
                                        return (
                                            <li key={c.sha} className="flex flex-col">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleCommit(commitKey)}
                                                    className="flex items-center gap-1 px-1 py-1 text-xs text-left w-full hover:bg-gray-800/80"
                                                    style={{paddingLeft: "16px"}}
                                                    aria-expanded={commitOpen}
                                                >
                                                    <span className="w-3 inline-block text-gray-300">
                                                        {commitOpen ? "▾" : "▸"}
                                                    </span>
                                                    <span className="font-mono text-[11px] shrink-0">
                                                        {shortSha(c.sha)}
                                                    </span>
                                                    {isLatest && (
                                                        <span
                                                            className="ml-1 px-1 rounded-sm text-[9px] uppercase tracking-wide bg-emerald-700 text-white shrink-0"
                                                            title="Most recent commit on this branch"
                                                        >
                                                            latest
                                                        </span>
                                                    )}
                                                    <span className="ml-auto text-[10px] text-gray-400 shrink-0">
                                                        {formatRelative(
                                                            // Prefer git timestamp from the sidecar
                                                            // (commit time); fall back to the blob
                                                            // mtime while sidecar is loading or
                                                            // missing. Matches the sort key.
                                                            props.sidecars.get(`${b.encodedBranch}/${c.sha}`)?.git.timestamp
                                                            || c.leaves[0]?.file.lastModified
                                                            || "",
                                                        )}
                                                    </span>
                                                </button>
                                                {commitOpen && (
                                                    <ul className="flex flex-col divide-y divide-gray-700/30">
                                                        {c.leaves.map((leaf) => {
                                                            const items = props.fileMenuItemsFor(leaf.file);
                                                            return (
                                                                <FileRow
                                                                    key={leaf.file.name}
                                                                    file={leaf.file}
                                                                    displayName={leaf.artefactName}
                                                                    indentLevel={2}
                                                                    viewingName={props.viewingName}
                                                                    loadedSourceNames={props.loadedSourceNames}
                                                                    conversionJobs={props.conversionJobs}
                                                                    expandedName={props.expandedName}
                                                                    setExpandedName={props.setExpandedName}
                                                                    onToggle={props.onToggle}
                                                                    setPickerName={props.setPickerName}
                                                                    selectionMode={props.selectionMode}
                                                                    isSelected={props.selection.has(leaf.file.name)}
                                                                    onLongPress={props.onLongPress}
                                                                    onSelectToggle={props.onSelectToggle}
                                                                    menuItems={items}
                                                                    onOpenContextMenu={(e) => props.onOpenContextMenu(e, items)}
                                                                    showModified={props.showModified}
                                                                />
                                                            );
                                                        })}
                                                    </ul>
                                                )}
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
};

export default StorageBrowser;
