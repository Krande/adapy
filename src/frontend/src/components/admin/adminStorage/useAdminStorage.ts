import {useEffect, useMemo, useRef, useState} from "react";
import {AdminFileEntry, ApiError, TargetFormat, viewerApi} from "@/services/viewerApi";
import {ensureConverted} from "@/services/conversion";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {
    buildFileTree,
    partitionUiHidden,
    FileTreeNode,
    FolderNode,
    loadExpandedFolders,
    saveExpandedFolders,
} from "@/utils/storage/fileTree";
import {OVERRIDE_KEYS, OverrideKey, OverrideTri, buildConversionOptions} from "./conversionOverrides";
import {STORAGE_COLUMNS, STORAGE_COL_WIDTHS_KEY, useResizableColumns} from "./useResizableColumns";

// State and actions of the admin storage tab: the enriched file listing for
// the currently-selected scope, folder expand state, multi-select, the
// per-conversion overrides, and every DL / Convert / Delete / Move action.
//
// Targets the *currently-selected scope* (read from scopeStore). To audit a
// different scope, the admin switches scope in the options drawer first; the
// table refetches.

export type StorageEntry =
    | {kind: "folder"; folder: FolderNode<AdminFileEntry>; depth: number; fileCount: number}
    | {kind: "file"; file: AdminFileEntry; depth: number};

export interface FolderPicker {
    title: string;
    initialNew?: string;
    onPick: (folder: string) => Promise<void> | void;
}

export function useAdminStorage() {
    const currentScope = useScopeStore((s) => s.current);
    const scope = scopeUrlPart(currentScope);
    const [allFiles, setAllFiles] = useState<AdminFileEntry[]>([]);
    // Published assets are hidden by default here too — the admin tab is a file
    // browser first, and a publishing scope holds thousands of blobs nobody
    // uploaded. The difference from the viewer's panel is that here they can be
    // switched back on, because this is where you come to look at storage
    // itself rather than at your own files.
    const [showHidden, setShowHidden] = useState(false);
    const {visible: shownFiles, hidden: hiddenFiles} = useMemo(
        () => partitionUiHidden(allFiles, (f) => f.key),
        [allFiles],
    );
    const files = showHidden ? allFiles : shownFiles;
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busyKey, setBusyKey] = useState<string | null>(null);
    const [expandedKey, setExpandedKey] = useState<string | null>(null);
    // Compression-sweep state. Server keeps the authoritative progress
    // in-process; we just poll it every 3 s while one's running.
    const [compressionBusy, setCompressionBusy] = useState(false);
    const [compressionMsg, setCompressionMsg] = useState<string | null>(null);
    // Multi-select for batch operations (currently just move-to-folder).
    // Tracks source keys; derived-blob rows aren't selectable since
    // moving a derived blob directly is rejected by the backend.
    const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set());
    const toggleKeySelection = (key: string) => {
        setSelectedKeys((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    };
    const clearSelection = () => setSelectedKeys(new Set());
    // Drop the selection when the scope changes — selecting in scope A
    // and moving in scope B would silently no-op (keys live per scope).
    useEffect(() => {
        clearSelection();
    }, [scope]);
    // Folder expand state for the admin storage tree, keyed by folder
    // path. Default: empty Set = everything collapsed, mirroring the
    // main StorageBrowser behaviour. Persisted per-scope under the
    // ``admin-storage`` namespace so it doesn't clobber the regular
    // panel's collapse state.
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
        () => loadExpandedFolders("admin-storage", scope),
    );
    useEffect(() => {
        setExpandedFolders(loadExpandedFolders("admin-storage", scope));
    }, [scope]);
    useEffect(() => {
        saveExpandedFolders("admin-storage", scope, expandedFolders);
    }, [scope, expandedFolders]);
    const toggleFolder = (path: string) => {
        setExpandedFolders((prev) => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };
    const [overrideOpen, setOverrideOpen] = useState(false);
    const [overrides, setOverrides] = useState<Record<OverrideKey, OverrideTri>>(() =>
        OVERRIDE_KEYS.reduce(
            (acc, {key}) => ({...acc, [key]: "unset"}),
            {} as Record<OverrideKey, OverrideTri>,
        ),
    );
    const activeOverrides = OVERRIDE_KEYS.filter(({key}) => overrides[key] !== "unset").length;

    // Track the in-flight reload so a second tap can supersede it
    // instead of being silently ignored. Without this, a hung
    // request would leave the button disabled forever and the user
    // would believe "nothing happened" — exactly the symptom we're
    // fixing here.
    const inflightRef = useRef<{seq: number; cancel: AbortController} | null>(null);
    const reloadSeq = useRef(0);
    // Min visible busy duration. The endpoint is ~30 ms; without a
    // floor the spinner blinks too fast for the eye to register and
    // the click feels like it did nothing.
    const MIN_BUSY_MS = 250;

    const reload = async () => {
        // Supersede any in-flight reload — a second tap means the
        // user wants fresh data right now, not the previous attempt.
        if (inflightRef.current) {
            inflightRef.current.cancel.abort();
        }
        const seq = ++reloadSeq.current;
        const cancel = new AbortController();
        inflightRef.current = {seq, cancel};
        setLoading(true);
        const startedAt = Date.now();
        try {
            const files = await viewerApi.adminListStorage(scope, {signal: cancel.signal});
            // Only the latest reload commits results — race-safe.
            if (reloadSeq.current === seq) {
                setAllFiles(files);
                setError(null);
            }
        } catch (e: unknown) {
            // Aborted requests aren't errors; the superseding tap
            // owns the UI now.
            if ((e as {name?: string}).name === "AbortError") return;
            if (reloadSeq.current === seq) {
                setError(e instanceof ApiError ? e.detail || e.message : String(e));
            }
        } finally {
            // Hold the busy state long enough for a person to see
            // the click registered, even on a 30 ms response.
            const elapsed = Date.now() - startedAt;
            const wait = Math.max(0, MIN_BUSY_MS - elapsed);
            if (wait > 0) await new Promise((r) => setTimeout(r, wait));
            if (reloadSeq.current === seq) {
                setLoading(false);
                inflightRef.current = null;
            }
        }
    };

    useEffect(() => {
        void reload();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope]);

    const onDownload = async (key: string, suggestedName: string) => {
        try {
            await viewerApi.downloadBlob(scope, key, suggestedName);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        }
    };

    const onConvert = async (sourceKey: string, target: TargetFormat) => {
        const stateKey = `${sourceKey}::${target}`;
        setBusyKey(stateKey);
        setError(null);
        try {
            const conversionOptions = buildConversionOptions(overrides);
            const derivedKey = await ensureConverted(scope, sourceKey, target, {
                conversionOptions,
            });
            const base = sourceKey.replace(/\.[^./]+$/, "");
            await viewerApi.downloadBlob(scope, derivedKey, `${base}.${target}`);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    const onDelete = async (key: string, label: string) => {
        if (!confirm(`Delete "${label}"? Any derived products are removed too.`)) return;
        setBusyKey(`${key}::delete`);
        try {
            await viewerApi.adminDeleteBlob(scope, key);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    // The picker modal drives every move action — bulk, per-row,
    // and the folder-kebab's move-into. ``onPick`` carries the chosen
    // destination; the modal handles existing-folder dropdown vs new-
    // folder text input. Rename keeps its plain window.prompt because
    // it's a sibling-name (no destination dropdown to offer).
    const [picker, setPicker] = useState<{
        title: string;
        initialNew?: string;
        onPick: (folder: string) => Promise<void> | void;
    } | null>(null);

    // Drag-resizable, persisted column widths for the desktop table.
    const {widths: colWidths, startResize, total: tableWidth} = useResizableColumns(
        STORAGE_COLUMNS,
        STORAGE_COL_WIDTHS_KEY,
    );

    // Per-row "Move to folder…" — operates on one source key. Source-
    // only (orphans excluded; they don't survive the rename).
    const onMoveSingleToFolder = (key: string) => {
        setPicker({
            title: `Move "${key}" to folder`,
            onPick: async (folder) => {
                setBusyKey(`${key}::move`);
                setError(null);
                try {
                    const result = await viewerApi.adminMoveKeysToFolder(scope, [key], folder);
                    if (result.failed.length > 0) {
                        setError(
                            result.failed.map((f) => `${f.key}: ${f.reason}`).join("\n"),
                        );
                    }
                    await reload();
                } catch (e) {
                    setError(e instanceof ApiError ? e.detail || e.message : String(e));
                } finally {
                    setBusyKey(null);
                }
            },
        });
    };

    // Rename or relocate a folder. ``mode`` decides whether to prompt
    // for a sibling name (window.prompt — no destination semantics
    // to pick from) or open the folder picker (move-into). Walks
    // every source key under the folder so derived blobs follow.
    const onFolderRenameOrMove = (
        folderPath: string,
        mode: "rename" | "moveInto",
    ) => {
        const runMove = async (newPath: string) => {
            if (newPath === folderPath) return;
            setBusyKey(`__folder_${mode}__:${folderPath}`);
            setError(null);
            try {
                const sourceKeys = files
                    .filter((f) => f.orphan !== true)
                    .map((f) => f.key);
                const result = await viewerApi.adminRenameOrMoveFolder(
                    scope, folderPath, newPath, sourceKeys,
                );
                if (result.failed.length > 0) {
                    setError(
                        result.failed.map((f) => `${f.key}: ${f.reason}`).join("\n"),
                    );
                }
                // Carry the expand state across the rename — collapse
                // the old path, open the new one — so the user lands
                // looking at their moved files instead of an
                // unexpanded entry.
                setExpandedFolders((prev) => {
                    const next = new Set(prev);
                    next.delete(folderPath);
                    next.add(newPath);
                    return next;
                });
                await reload();
            } catch (e) {
                setError(e instanceof ApiError ? e.detail || e.message : String(e));
            } finally {
                setBusyKey(null);
            }
        };

        if (mode === "rename") {
            const input = window.prompt(
                `Rename folder "${folderPath}" to (sibling name, no slashes):`,
                "",
            );
            if (input === null) return;
            const trimmedInput = input.trim().replace(/^\/+|\/+$/g, "");
            if (!trimmedInput) {
                setError("Destination required");
                return;
            }
            if (trimmedInput.includes("/")) {
                setError("Rename name must not contain slashes — use Move folder into… instead");
                return;
            }
            const lastSlash = folderPath.lastIndexOf("/");
            const parent = lastSlash >= 0 ? folderPath.slice(0, lastSlash) : "";
            void runMove(parent ? `${parent}/${trimmedInput}` : trimmedInput);
            return;
        }

        const basename = folderPath.split("/").pop() ?? folderPath;
        setPicker({
            title: `Move folder "${folderPath}" into`,
            onPick: async (dest) => {
                await runMove(`${dest}/${basename}`);
            },
        });
    };

    const onMoveSelectedToFolder = () => {
        if (selectedKeys.size === 0) return;
        const count = selectedKeys.size;
        setPicker({
            title: `Move ${count} file${count === 1 ? "" : "s"} to folder`,
            onPick: async (folder) => {
                setBusyKey("__bulk_move__");
                setError(null);
                try {
                    const result = await viewerApi.adminMoveKeysToFolder(
                        scope,
                        Array.from(selectedKeys),
                        folder,
                    );
                    if (result.failed.length > 0) {
                        const summary = result.failed
                            .map((f) => `${f.key}: ${f.reason}`)
                            .join("\n");
                        setError(
                            `Moved ${result.moved.length} of ${
                                result.moved.length + result.failed.length
                            }; failures:\n${summary}`,
                        );
                    }
                    clearSelection();
                    await reload();
                } catch (e) {
                    setError(e instanceof ApiError ? e.detail || e.message : String(e));
                } finally {
                    setBusyKey(null);
                }
            },
        });
    };

    // Delete a single derived blob without touching the source.
    // Common during conversion debugging: remove the cached GLB so
    // the next /convert re-runs the pipeline instead of returning the
    // cached output. The admin endpoint already routes derived keys
    // to a one-blob delete; the source delete path is unaffected.
    const onDeleteDerived = async (sourceKey: string, derivedKey: string, label: string) => {
        if (!confirm(`Delete cached "${label}"? Next Convert will regenerate it.`)) return;
        setBusyKey(`${derivedKey}::delete`);
        try {
            await viewerApi.adminDeleteBlob(scope, derivedKey);
            await reload();
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusyKey(null);
        }
    };

    // Bulk-delete the derived blobs for a single source. The backend
    // /admin DELETE endpoint is one-blob-per-call, so we fan out the
    // per-derived deletes in parallel — keys are independent and the
    // browser caps concurrency at ~6 per origin anyway. Errors are
    // collected and surfaced as a summary so a partial failure
    // doesn't leave the user guessing which blobs survived.
    const onDeleteAllDerived = async (file: AdminFileEntry) => {
        if (file.derived.length === 0) return;
        const n = file.derived.length;
        if (!confirm(
            `Delete all ${n} cached derived product${n === 1 ? "" : "s"} for "${file.key}"? ` +
            `Sources are not touched; next Convert will regenerate them.`,
        )) return;
        setBusyKey(`${file.key}::delete-all-derived`);
        const failures: string[] = [];
        try {
            const results = await Promise.allSettled(
                file.derived.map((d) => viewerApi.adminDeleteBlob(scope, d.key)),
            );
            results.forEach((r, i) => {
                if (r.status === "rejected") {
                    const reason = r.reason instanceof ApiError
                        ? r.reason.detail || r.reason.message
                        : String(r.reason);
                    failures.push(`${file.derived[i].key}: ${reason}`);
                }
            });
            if (failures.length) {
                setError(
                    `Deleted ${n - failures.length} of ${n}; failures:\n${failures.join("\n")}`,
                );
            } else {
                setError(null);
            }
            await reload();
        } finally {
            setBusyKey(null);
        }
    };

    // Wipe every cached derived product across every source in the
    // current scope. Costs nothing irrecoverable — sources stay put,
    // Convert regenerates on demand. Useful after a conversion-
    // pipeline change when every cached blob is potentially stale.
    const onClearAllDerived = async () => {
        const allDerived = files.flatMap((f) =>
            f.derived.map((d) => ({source: f.key, derivedKey: d.key})),
        );
        const total = allDerived.length;
        if (total === 0) return;
        if (!confirm(
            `Clear ALL ${total} cached derived product${total === 1 ? "" : "s"} ` +
            `in scope "${currentScope?.name ?? "Shared"}"? ` +
            `Sources are preserved; next Convert regenerates them.`,
        )) return;
        setBusyKey("__clear_all_derived__");
        const failures: string[] = [];
        try {
            const results = await Promise.allSettled(
                allDerived.map((d) => viewerApi.adminDeleteBlob(scope, d.derivedKey)),
            );
            results.forEach((r, i) => {
                if (r.status === "rejected") {
                    const reason = r.reason instanceof ApiError
                        ? r.reason.detail || r.reason.message
                        : String(r.reason);
                    failures.push(`${allDerived[i].derivedKey}: ${reason}`);
                }
            });
            if (failures.length) {
                setError(
                    `Cleared ${total - failures.length} of ${total}; failures:\n${failures.join("\n")}`,
                );
            } else {
                setError(null);
            }
            await reload();
        } finally {
            setBusyKey(null);
        }
    };
    const totalDerivedAcrossScope = files.reduce((acc, f) => acc + f.derived.length, 0);

    // Flatten the folder tree into the rendering list. Folders are
    // always emitted; their contents only when the folder is
    // expanded. ``depth`` drives the Name-column indent so a nested
    // tree reads cleanly across the wide admin table.
    const visibleEntries = useMemo(() => {
        const tree = buildFileTree(files, (f) => f.key);
        const out: Array<
            | {kind: "folder"; folder: FolderNode<AdminFileEntry>; depth: number; fileCount: number}
            | {kind: "file"; file: AdminFileEntry; depth: number}
        > = [];
        const countFiles = (n: FileTreeNode<AdminFileEntry>): number =>
            n.kind === "file" ? 1 : n.children.reduce((s, c) => s + countFiles(c), 0);
        const walk = (nodes: FileTreeNode<AdminFileEntry>[], depth: number) => {
            for (const node of nodes) {
                if (node.kind === "folder") {
                    out.push({
                        kind: "folder",
                        folder: node,
                        depth,
                        fileCount: countFiles(node),
                    });
                    if (expandedFolders.has(node.path)) {
                        walk(node.children, depth + 1);
                    }
                } else {
                    out.push({kind: "file", file: node.file, depth});
                }
            }
        };
        walk(tree, 0);
        return out;
    }, [files, expandedFolders]);

    // Kick off a server-side sweep that gzips any source-format file
    // that's stored uncompressed (typical case: a >200 MB upload took
    // the direct presigned-PUT path before the browser-side
    // compression bit landed). Server returns 202 immediately; the
    // global compression-progress toast (poller in RestModeUI) picks
    // up progress and displays it bottom-right. Idempotent.
    const onCompressUncompressed = async () => {
        setCompressionBusy(true);
        try {
            await viewerApi.adminStartCompressionSweep(scope);
            setCompressionMsg("Started — see progress toast (bottom-right)");
            setTimeout(() => setCompressionMsg(null), 5000);
        } catch (e) {
            const msg = e instanceof ApiError ? (e.detail || e.message) : String(e);
            setError(`compress sweep start failed: ${msg}`);
        } finally {
            setCompressionBusy(false);
        }
    };

    return {
        currentScope,
        scope,
        files,
        hiddenFiles,
        showHidden,
        setShowHidden,
        loading,
        error,
        setError,
        busyKey,
        expandedKey,
        setExpandedKey,
        compressionBusy,
        compressionMsg,
        selectedKeys,
        toggleKeySelection,
        clearSelection,
        expandedFolders,
        toggleFolder,
        overrideOpen,
        setOverrideOpen,
        overrides,
        setOverrides,
        activeOverrides,
        reload,
        onDownload,
        onConvert,
        onDelete,
        picker,
        setPicker,
        colWidths,
        startResize,
        tableWidth,
        onMoveSingleToFolder,
        onFolderRenameOrMove,
        onMoveSelectedToFolder,
        onDeleteDerived,
        onDeleteAllDerived,
        onClearAllDerived,
        totalDerivedAcrossScope,
        visibleEntries,
        onCompressUncompressed,
    };
}
