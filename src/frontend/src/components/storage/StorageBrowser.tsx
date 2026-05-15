import React, {useEffect, useRef, useState} from "react";
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
import UploadIcon from "../icons/UploadIcon";
import FolderClosedIcon from "../icons/FolderClosedIcon";
import FolderOpenIcon from "../icons/FolderOpenIcon";
import ChevronRightIcon from "../icons/ChevronRightIcon";
import FieldPickerModal from "./FieldPickerModal";
import GitHistoryPanel from "./GitHistoryPanel";
import {BuildSidecar, useBuildSidecars} from "@/hooks/useBuildSidecars";
import {
    buildFileTree,
    FileTreeNode,
    FolderNode,
    loadExpandedFolders,
    saveExpandedFolders,
} from "@/utils/storage/fileTree";
import {KebabMenuItem, RowKebabMenu} from "@/components/common/RowKebabMenu";
import {viewerApi} from "@/services/viewerApi";

// Files that carry per-(step, field) result data and benefit from the
// picker UI. SIF is the only one in REST mode today; new formats land
// here when their converter learns to honor (step, field).
function isFEAResult(name: string): boolean {
    return name.toLowerCase().endsWith(".sif");
}

// Files that flow through the streaming-viewer artefact bake (mesh
// GLB + per-field blobs + manifest). Static set: .sif and .rmed are
// adapy-native streaming sources. Capability workers (e.g. abaqus
// .odb / .sqlite) advertise additional extensions through
// /api/config → window.STREAMING_ONLY_EXTS; honoring that here is
// what keeps a plug-in's stream-readable formats from accidentally
// hitting the legacy /convert pipeline (415) on click.
function isStreamingFEAResult(name: string): boolean {
    const lower = name.toLowerCase();
    if (lower.endsWith(".sif") || lower.endsWith(".rmed")) return true;
    for (const e of runtime.streamingOnlyExts()) {
        const norm = e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`;
        if (lower.endsWith(norm)) return true;
    }
    return false;
}

// Files the legacy "load into scene" checkbox can handle — those
// that have a usable GLB target via the legacy convert pipeline.
// Mirror of ada.comms.rest.converter.supported_targets_for: anything
// in _STREAMING_FEA_EXTS (or a worker-advertised streaming-only
// extension) has no legacy GLB target, only the streaming bake. The
// toggle path routes those through ``load_fea_with_defaults``
// instead of disabling the checkbox.
function canLoadIntoSceneLegacy(name: string): boolean {
    const lower = name.toLowerCase();
    if (lower.endsWith(".rmed")) return false;
    for (const e of runtime.streamingOnlyExts()) {
        const norm = e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`;
        if (lower.endsWith(norm)) return false;
    }
    return true;
}

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
    const [bulkBusy, setBulkBusy] = useState<"load" | "unload" | "clear" | null>(null);
    const [gitHistoryOpen, setGitHistoryOpen] = useState(false);
    // Multi-select mode: a Set of file names. Empty set = mode off.
    // Entered by long-press on any FileRow (or via the "Select" button
    // in the header on desktop where long-press is unergonomic). Tap
    // toggles set membership while in mode; the existing per-row
    // checkbox is hidden so the row itself is the tap target.
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
    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);

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
        const targets = files.filter((f) => selection.has(f.name) && !loadedSourceNames.has(f.name));
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

    const onFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (!file) return;
        setUploading(true);
        setUploadName(file.name);
        setUploadLoaded(0);
        setUploadTotal(file.size);
        try {
            await uploadFile(file, {
                onProgress: (loaded, total) => {
                    setUploadLoaded(loaded);
                    if (total) setUploadTotal(total);
                },
            });
        } catch (err) {
            console.error("upload failed", err);
        } finally {
            setUploading(false);
            setUploadName(null);
            setUploadLoaded(0);
            setUploadTotal(0);
        }
    };

    return (
        <div
            data-no-upload-menu
            // Match ObjectInfoBox styling. The viewport-clamped max-width
            // makes the panel self-contain regardless of what the panel-row
            // does — on mobile it stays inside the viewport even if the
            // parent's width hasn't resolved. min-w-0 lets it actually
            // shrink below intrinsic content width so the header buttons
            // don't push past the right edge.
            className="bg-gray-400 bg-opacity-50 rounded p-2 w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md"
        >
            <div className="flex justify-between items-center gap-2 mb-2">
                <div className="min-w-0 flex-1">
                    <h2 className="font-bold truncate">Storage</h2>
                    {/* Show the active scope so it's clear which space
                        this list reflects. Files uploaded under one
                        scope are invisible to a list query under another
                        — surfacing the name avoids the "I uploaded but
                        nothing shows" confusion when scope drifts. */}
                    <div className="text-[10px] uppercase tracking-wide text-gray-200/80 truncate"
                         title={currentScope?.kind ? `${currentScope.kind}${currentScope.id ? ":" + currentScope.id : ""}` : "shared"}>
                        scope: {currentScope?.name ?? "Shared"}
                    </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept={uploadAcceptAttr()}
                        style={{display: "none"}}
                        onChange={onFilePicked}
                    />
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white p-1 rounded disabled:opacity-60 flex items-center justify-center"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploading}
                        title="Upload a file to this scope"
                        aria-label="Upload file"
                    >
                        {uploading ? <Spinner/> : <UploadIcon/>}
                    </button>
                    <button
                        type="button"
                        className={
                            "bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white rounded " +
                            "flex items-center justify-center " +
                            // 40px+ tap target on mobile per WCAG; tighter
                            // on desktop where the cursor is precise.
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-none focus:ring-2 focus:ring-blue-400"
                        }
                        onClick={onRefresh}
                        title={refreshing ? "Refreshing — tap again to retry" : "Refresh file list"}
                        aria-label="Refresh list"
                        aria-busy={refreshing}
                    >
                        <span className={refreshing ? "animate-spin" : ""}>
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
                                "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-60 " +
                                "text-white rounded text-xs whitespace-nowrap " +
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
                </div>
            </div>
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
                    <div className="mt-1 h-1 w-full bg-gray-300/50 rounded overflow-hidden">
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
            {files.length === 0 ? (
                <div className="text-xs italic">
                    No files yet. Use the Upload button to add one.
                </div>
            ) : (
                (() => {
                    const {regular, branches} = classifyFiles(files, sidecars);
                    return (
                        <div className="flex flex-col max-h-80 overflow-auto">
                            {regular.length > 0 && (() => {
                                const tree = buildFileTree(regular, (f) => f.name);
                                const renderNode = (
                                    node: ServerFileTreeNode,
                                    depth: number,
                                ): React.ReactNode => {
                                    if (node.kind === "file") {
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
                                            />
                                        );
                                    }
                                    const expanded = expandedFolders.has(node.path);
                                    const total = countFiles(node);
                                    return (
                                        <React.Fragment key={`folder:${node.path}`}>
                                            <FolderRow
                                                folder={node}
                                                depth={depth}
                                                expanded={expanded}
                                                fileCount={total}
                                                onToggle={() => toggleFolder(node.path)}
                                            />
                                            {expanded &&
                                                node.children.map((c) =>
                                                    renderNode(c, depth + 1),
                                                )}
                                        </React.Fragment>
                                    );
                                };
                                return (
                                    <ul className="flex flex-col divide-y divide-gray-500/40">
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
                                />
                            )}
                        </div>
                    );
                })()
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
            {inSelectionMode && (
                <div
                    className={
                        // Sticky inside the panel rather than fixed to
                        // viewport — keeps the action bar bound to the
                        // storage panel's footprint on desktop while
                        // still pinning to the bottom on mobile where
                        // the panel takes the full screen anyway.
                        "mt-2 -mx-2 -mb-2 px-2 py-2 border-t border-gray-500/40 bg-gray-700/95 " +
                        "rounded-b flex items-center gap-2 sticky bottom-0"
                    }
                >
                    <span className="text-xs text-white whitespace-nowrap">
                        {selection.size} selected
                    </span>
                    {/* Load is the primary action in this bar
                        (affirmative — adds models to the scene), so
                        it gets the same blue as Upload / Refresh.
                        Unload + Cancel are neutral gray; the
                        amber row highlight already conveys "these
                        rows are armed for an action", no need for
                        red to scream "destructive". */}
                    <button
                        type="button"
                        onClick={() => void onLoadSelected()}
                        disabled={bulkBusy !== null}
                        className="bg-blue-700 hover:bg-blue-600 active:bg-blue-800 disabled:opacity-60 text-white text-xs px-2 py-1 rounded min-h-[36px]"
                    >
                        {bulkBusy === "load" ? "Loading…" : "Load"}
                    </button>
                    <button
                        type="button"
                        onClick={onUnloadSelected}
                        disabled={bulkBusy !== null}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-60 text-white text-xs px-2 py-1 rounded min-h-[36px]"
                    >
                        {bulkBusy === "unload" ? "Unloading…" : "Unload"}
                    </button>
                    <button
                        type="button"
                        onClick={clearSelection}
                        disabled={bulkBusy !== null}
                        className="ml-auto bg-gray-600 hover:bg-gray-500 disabled:opacity-60 text-white text-xs px-2 py-1 rounded min-h-[36px]"
                    >
                        Cancel
                    </button>
                </div>
            )}
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
    onToggle: () => void;
}

const FolderRow: React.FC<FolderRowProps> = ({
    folder,
    depth,
    expanded,
    fileCount,
    onToggle,
}) => {
    const indentPx = depth * 12;
    return (
        <li
            className="flex items-center gap-1.5 px-2 py-1 cursor-pointer hover:bg-gray-300/10 select-none"
            style={{paddingLeft: 8 + indentPx}}
            onClick={onToggle}
            role="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} folder ${folder.name}`}
        >
            {/* Chevron — single right-pointing icon rotated 90° on
                expand. text-blue-300 picks up the same accent the
                row's loaded-state and progress bar use, so the
                affordance reads as part of the toolbar palette
                rather than a stray gray triangle. */}
            <ChevronRightIcon
                className={
                    "shrink-0 text-blue-300 transition-transform duration-150 " +
                    (expanded ? "rotate-90" : "")
                }
            />
            {/* Folder glyph swaps closed↔open with the expand state.
                Same blue tone so eye + chevron read as one
                composite control. */}
            {expanded ? (
                <FolderOpenIcon className="shrink-0 text-blue-300"/>
            ) : (
                <FolderClosedIcon className="shrink-0 text-blue-300"/>
            )}
            <span className="text-xs flex-1 min-w-0 truncate font-semibold">
                {folder.name}/
            </span>
            <span className="text-[10px] text-gray-400 shrink-0">
                {fileCount}
            </span>
        </li>
    );
};

// Per-row kebab items for the main StorageBrowser. Mirrors the
// admin tab's ``buildSourceMenuItems`` shape but covers only the
// actions a non-admin user can take: download the source bytes,
// copy the storage key, and (for legacy non-streaming FEA) open
// the step / field picker. The load/unload toggle stays on the
// checkbox — having it in the kebab too would be redundant.
function buildFileRowMenuItems(args: {
    fileName: string;
    displayName: string;
    scopeUrl: string;
    setPickerName: (n: string | null) => void;
}): KebabMenuItem[] {
    const {fileName, displayName, scopeUrl, setPickerName} = args;
    const items: KebabMenuItem[] = [];

    if (runtime.isRestMode()) {
        items.push({
            key: "download",
            label: "Download source",
            onClick: () => {
                // Suggested filename is just the last segment so the
                // browser doesn't propose a path-shaped name.
                const suggested = fileName.split("/").pop() || fileName;
                void viewerApi.downloadBlob(scopeUrl, fileName, suggested);
            },
        });
    }

    items.push({
        key: "copy-key",
        label: "Copy storage key",
        title: "Copy the full S3-style key to the clipboard",
        onClick: () => {
            void navigator.clipboard?.writeText(fileName);
        },
    });

    // Legacy step/field picker — only meaningful for non-streaming
    // FEA formats. Streaming FEA (SIF / RMED) goes through
    // load_fea_with_defaults via the toggle, so a picker entry
    // would just confuse the user with two parallel ways to load.
    if (
        isFEAResult(fileName)
        && !isStreamingFEAResult(fileName)
        && runtime.isRestMode()
        && runtime.convertEnabled()
    ) {
        items.push({
            key: "pick-step-field",
            label: "Pick step / field…",
            separatorBefore: true,
            onClick: () => setPickerName(fileName),
        });
    }

    // Reference displayName so callers can pass it for future
    // surfaced labels without lint complaining about an unused
    // arg.
    void displayName;

    return items;
}

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
}) => {
    // Read the active scope so the kebab's Download action knows
    // which storage namespace the key lives in. Component-scoped
    // subscription is cheap — one selector per row, no extra renders
    // unless scope actually changes.
    const currentScope = useScopeStore((s) => s.current);
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
                "flex flex-col px-1 py-1 text-xs " +
                (selectionMode
                    ? "cursor-pointer " + (isSelected ? "bg-amber-700/30" : "hover:bg-amber-700/10")
                    : "")
            }
            style={indentPx ? {paddingLeft: `${4 + indentPx}px`} : undefined}
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
                }
            }}
        >
            <div className="flex items-center justify-between gap-2">
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
                    <label
                        className={
                            "flex items-center gap-2 cursor-pointer select-none px-1 py-1 -mx-1 -my-1 " +
                            (isLoaded ? "text-blue-200 font-medium" : "")
                        }
                        title={
                            isViewing
                                ? "Loading…"
                                : otherViewing
                                    ? "Another file is loading"
                                    : isLoaded
                                        ? "Loaded in scene — uncheck to remove"
                                        : isStreamingFEAResult(f.name)
                                            ? "Open in streaming FEA viewer (default field, configure in Simulation controls)"
                                            : "Add to scene (overlays alongside any other loaded files)"
                        }
                    >
                        <input
                            type="checkbox"
                            className="h-5 w-5 shrink-0 cursor-pointer disabled:cursor-not-allowed"
                            checked={isLoaded}
                            onChange={(e) => onToggle(f, e.target.checked)}
                            disabled={
                                isViewing || otherViewing ||
                                // The legacy GLB toggle still gates non-streaming
                                // sources without a usable convert target.
                                (!isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name))
                            }
                            aria-busy={isViewing || undefined}
                        />
                    </label>
                )}
                <button
                    type="button"
                    onClick={(e) => {
                        if (selectionMode) {
                            // Selection mode: row click already handles
                            // it; the inner button is just here for
                            // truncation toggling normally. Bail.
                            e.stopPropagation();
                            onSelectToggle(f.name);
                            return;
                        }
                        setExpandedName(expandedName === f.name ? null : f.name);
                    }}
                    className={`flex-1 min-w-0 text-left ${expandedName === f.name ? 'whitespace-normal break-all' : 'truncate'} ${isLoaded ? 'text-blue-200 font-medium' : ''}`}
                    title={f.name}
                >
                    {displayName}
                </button>
                <div className="flex items-center gap-1 shrink-0">
                    {isViewing && <Spinner/>}
                    {!selectionMode && (
                        <RowKebabMenu
                            ariaLabel={`More actions for ${displayName}`}
                            disabled={otherViewing || isViewing}
                            buttonClassName="h-7 w-7 text-white hover:bg-gray-300/40"
                            items={buildFileRowMenuItems({
                                fileName: f.name,
                                displayName,
                                scopeUrl: scopeUrlPart(currentScope),
                                setPickerName,
                            })}
                        />
                    )}
                </div>
            </div>
            {isViewing && (
                <div className="mt-1 h-1 w-full bg-gray-300/50 rounded overflow-hidden">
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
        <div className="border-t border-gray-500/40 pt-1 mt-1">
            <div className="flex items-center justify-between px-1 pb-1">
                <div className="text-[10px] uppercase tracking-wide text-gray-200/70">
                    Versions
                </div>
                <button
                    type="button"
                    onClick={props.onOpenGitHistory}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-white"
                    title="Open chronological commit timeline with author + parent links"
                >
                    Git history
                </button>
            </div>
            <ul className="flex flex-col divide-y divide-gray-500/30">
                {branches.map((b, bIdx) => {
                    const branchOpen = openBranches.has(b.encodedBranch);
                    return (
                        <li key={b.encodedBranch} className="flex flex-col">
                            <button
                                type="button"
                                onClick={() => toggleBranch(b.encodedBranch)}
                                className="flex items-center gap-1 px-1 py-1 text-xs text-left w-full hover:bg-gray-300/10"
                                aria-expanded={branchOpen}
                                title={b.displayBranch}
                            >
                                <span className="w-3 inline-block text-gray-300">
                                    {branchOpen ? "▾" : "▸"}
                                </span>
                                <span className="font-mono text-[11px] truncate flex-1 min-w-0">
                                    {b.displayBranch}
                                </span>
                                <span className="text-[10px] text-gray-300/80 shrink-0">
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
                                                    className="flex items-center gap-1 px-1 py-1 text-xs text-left w-full hover:bg-gray-300/10"
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
                                                            className="ml-1 px-1 rounded text-[9px] uppercase tracking-wide bg-emerald-700 text-white shrink-0"
                                                            title="Most recent commit on this branch"
                                                        >
                                                            latest
                                                        </span>
                                                    )}
                                                    <span className="ml-auto text-[10px] text-gray-300/80 shrink-0">
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
                                                    <ul className="flex flex-col divide-y divide-gray-500/20">
                                                        {c.leaves.map((leaf) => (
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
                                                            />
                                                        ))}
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
