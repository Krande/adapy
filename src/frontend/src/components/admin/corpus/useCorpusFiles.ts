import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {Corpus, FileEntry, viewerApi} from "@/services/viewerApi";
import {
    collectFolderPaths,
    loadPendingFolders,
    previewKeyList,
    savePendingFolders,
} from "@/utils/storage/fileTree";
import type {FileTreeMutations} from "../FileTreeView";
import {OP_CHUNK, ViewMode, basenameOf, dirnameOf, normKey} from "./shared";

// State and actions for one corpus's files: listing, tree/flat view, pending
// folders, multi-select, uploads (with retry), and every rename / move /
// delete / copy through the admin endpoints (corpus scopes are admin-only on
// every axis).

export interface CorpusFolderPicker {
    title: string;
    allowRoot?: boolean;
    submitLabel?: string;
    onPick: (folder: string) => Promise<void> | void;
}

export interface UploadFailures {
    folder?: string;
    failed: Array<{file: File; reason: string}>;
}

export function useCorpusFiles(corpus: Corpus, onMetaUpdated: () => void) {
    const scope = `corpus:${corpus.slug}`;
    const [files, setFiles] = useState<FileEntry[]>([]);
    const [err, setErr] = useState<string | null>(null);
    // Transient success line (e.g. copy-to-personal outcome).
    const [note, setNote] = useState<string | null>(null);
    // In-flight batch operation (move / delete / copy) — rendered as a
    // spinner status bar under the button row so a drag-drop move of
    // many files visibly runs until the listing refreshes. The ref
    // mirrors it so callbacks can reject overlapping batches without
    // stale-closure issues (concurrent moves would race server-side).
    const [busy, setBusy] = useState<string | null>(null);
    const busyRef = useRef(false);
    const beginOp = (msg: string): boolean => {
        if (busyRef.current) return false;
        busyRef.current = true;
        setBusy(msg);
        return true;
    };
    const updateOp = (msg: string) => setBusy(msg);
    const endOp = () => {
        busyRef.current = false;
        setBusy(null);
    };
    const [uploading, setUploading] = useState<string | null>(null);
    const [progress, setProgress] = useState(0);
    const [copyOpen, setCopyOpen] = useState(false);
    // Flat ⇄ tree representation, persisted per corpus so a chosen view
    // sticks across reloads / corpus switches.
    const [viewMode, setViewMode] = useState<ViewMode>(() => {
        try {
            return window.localStorage.getItem(`ada.corpus.viewMode.${corpus.slug}`) === "tree" ? "tree" : "flat";
        } catch {
            return "flat";
        }
    });
    useEffect(() => {
        try {
            window.localStorage.setItem(`ada.corpus.viewMode.${corpus.slug}`, viewMode);
        } catch {
            // localStorage full / disabled — fall back to in-memory only.
        }
    }, [corpus.slug, viewMode]);
    const inputRef = useRef<HTMLInputElement>(null);

    const reload = useCallback(async () => {
        try {
            const xs = await viewerApi.listFiles(scope);
            setFiles(xs);
            setErr(null);
        } catch (e) {
            setErr((e as Error).message || "listing failed");
        }
    }, [scope]);

    useEffect(() => { void reload(); }, [reload]);

    // Client-side "pending" empty folders — storage is prefix-based so
    // they have no server representation until a file lands in them.
    // Persisted per corpus scope; pruned once a real key appears
    // underneath (same mechanics as StorageBrowser).
    const [pendingFolders, setPendingFolders] = useState<string[]>(
        () => loadPendingFolders("corpus", scope),
    );
    useEffect(() => {
        savePendingFolders("corpus", scope, pendingFolders);
    }, [scope, pendingFolders]);
    useEffect(() => {
        setPendingFolders((prev) => {
            const next = prev.filter(
                (p) => !files.some((f) => normKey(f.key).startsWith(p + "/")),
            );
            return next.length === prev.length ? prev : next;
        });
    }, [files]);
    const removePendingFoldersUnder = (path: string) => {
        setPendingFolders((prev) =>
            prev.filter((p) => p !== path && !p.startsWith(path + "/")),
        );
    };
    // Rename/move of a pending (empty) folder is pure client state.
    const rekeyPendingFolders = (oldPath: string, newPath: string) => {
        setPendingFolders((prev) => prev.map((p) => (
            p === oldPath
                ? newPath
                : p.startsWith(oldPath + "/")
                    ? newPath + p.slice(oldPath.length)
                    : p
        )));
    };
    const folderHasKeys = useCallback(
        (path: string) => files.some((f) => normKey(f.key).startsWith(path + "/")),
        [files],
    );

    // Inline name/description editor in the header. The slug is
    // immutable (storage prefix + scope URLs hang off it), so only the
    // display fields are editable. Seeded from the current corpus row
    // each time the editor opens.
    const [editingMeta, setEditingMeta] = useState(false);
    const [metaName, setMetaName] = useState("");
    const [metaDesc, setMetaDesc] = useState("");
    const [metaBusy, setMetaBusy] = useState(false);
    const openMetaEdit = () => {
        setMetaName(corpus.name);
        setMetaDesc(corpus.description ?? "");
        setEditingMeta(true);
    };
    const saveMeta = async () => {
        const name = metaName.trim();
        if (!name) {
            setErr("name required");
            return;
        }
        setMetaBusy(true);
        try {
            await viewerApi.adminCorpusUpdate(corpus.slug, {
                name,
                description: metaDesc.trim() || null,
            });
            setEditingMeta(false);
            setErr(null);
            onMetaUpdated();
        } catch (e) {
            setErr((e as Error).message || "corpus update failed");
        } finally {
            setMetaBusy(false);
        }
    };

    // Where the tree's "new folder" inline input shows ("" = top level).
    const [newFolderAt, setNewFolderAt] = useState<string | null>(null);
    // Destination-folder modal shared by the upload and move flows.
    const [picker, setPicker] = useState<{
        title: string;
        allowRoot?: boolean;
        submitLabel?: string;
        onPick: (folder: string) => Promise<void> | void;
    } | null>(null);

    // Multi-select (tree mode): checkbox / shift+arrow selection set
    // feeding the bulk Move/Delete toolbar. Dragging a selected row
    // drags the whole set (FileTreeView handles that).
    const [selected, setSelected] = useState<Set<string>>(() => new Set());
    const setSelection = useCallback((keys: string[], select: boolean) => {
        setSelected((prev) => {
            const next = new Set(prev);
            if (select) keys.forEach((k) => next.add(k));
            else keys.forEach((k) => next.delete(k));
            return next;
        });
    }, []);
    const clearSelection = () => setSelected(new Set());
    // Drop selection entries whose keys vanished (moved/renamed/deleted).
    useEffect(() => {
        setSelected((prev) => {
            const live = new Set(files.map((f) => f.key));
            const next = new Set(Array.from(prev).filter((k) => live.has(k)));
            return next.size === prev.size ? prev : next;
        });
    }, [files]);

    const existingFolderPaths = useMemo(
        () => Array.from(new Set([
            ...collectFolderPaths(files, (f) => f.key),
            ...pendingFolders,
        ])).sort((a, b) => a.localeCompare(b)),
        [files, pendingFolders],
    );

    // Failed uploads from the last batch — drives the retry dialog.
    // Holds the actual File objects so Retry can re-attempt without
    // re-picking them from disk.
    const [uploadFailures, setUploadFailures] = useState<{
        folder?: string;
        failed: Array<{file: File; reason: string}>;
    } | null>(null);

    // Upload a batch sequentially into an optional folder prefix. Pin
    // autoConvert:false so we don't auto-generate derived blobs for
    // corpus uploads — the audit dispatcher does that on demand when
    // the sweep fires. Files whose destination key already exists in
    // the corpus are skipped, never overwritten (same semantics as the
    // copy flows). A failed file doesn't abort the batch — failures
    // land in the retry dialog.
    const uploadFilesTo = useCallback(async (list: File[], folder?: string) => {
        if (list.length === 0) return;
        setErr(null);
        setNote(null);
        const existing = new Set(files.map((f) => normKey(f.key)));
        const targetKey = (file: File) => (folder ? `${folder}/${file.name}` : file.name);
        const skipped = list.filter((f) => existing.has(targetKey(f)));
        const toUpload = list.filter((f) => !existing.has(targetKey(f)));
        const {uploadFile} = await import("@/utils/scene/handlers/upload_source_file");
        const failed: Array<{file: File; reason: string}> = [];
        for (let i = 0; i < toUpload.length; i++) {
            const file = toUpload[i];
            setUploading(toUpload.length > 1 ? `${file.name} (${i + 1}/${toUpload.length})` : file.name);
            setProgress(0);
            try {
                await uploadFile(file, {
                    autoConvert: false,
                    scope,
                    folder,
                    onProgress: (loaded, total) => setProgress(total > 0 ? loaded / total : 0),
                });
            } catch (e) {
                failed.push({file, reason: (e as Error).message || "upload failed"});
            }
        }
        setUploading(null);
        setProgress(0);
        await reload();
        const bits: string[] = [];
        const uploaded = toUpload.length - failed.length;
        if (uploaded > 0) bits.push(`uploaded ${uploaded}`);
        if (skipped.length > 0) bits.push(`skipped ${skipped.length} (already in corpus)`);
        if (bits.length > 0) setNote(bits.join(" · "));
        if (failed.length > 0) setUploadFailures({folder, failed});
    }, [files, scope, reload]);

    // Upload button flow: pick the files first, then prompt for the
    // destination folder — an existing folder, a new path, or the top
    // level (the default).
    const onPickUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const picked = Array.from(e.target.files ?? []);
        if (inputRef.current) inputRef.current.value = "";
        if (picked.length === 0) return;
        setPicker({
            title: `Upload ${picked.length} file${picked.length === 1 ? "" : "s"} to`,
            allowRoot: true,
            submitLabel: "Upload",
            onPick: (folder) => void uploadFilesTo(picked, folder || undefined),
        });
    }, [uploadFilesTo]);

    const onDelete = useCallback(async (key: string) => {
        if (!confirm(`Delete ${key} from ${corpus.slug}? This can't be undone.`)) return;
        try {
            await viewerApi.adminDeleteBlob(scope, key);
            await reload();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
        }
    }, [scope, corpus.slug, reload]);

    const alertFailures = (failed: Array<{key: string; reason: string}>) => {
        if (failed.length > 0) {
            window.alert(failed.map((f) => `${f.key}: ${f.reason}`).join("\n"));
        }
    };

    const runFolderMove = useCallback(async (folderPath: string, newPath: string) => {
        if (newPath === folderPath) return;
        const count = files.filter((f) => normKey(f.key).startsWith(folderPath + "/")).length;
        if (!beginOp(
            `Moving folder "${folderPath}" → "${newPath}" (${count} file${count === 1 ? "" : "s"})…`,
        )) return;
        try {
            const allKeys = files.map((f) => f.key);
            const r = await viewerApi.adminRenameOrMoveFolder(scope, folderPath, newPath, allKeys);
            alertFailures(r.failed);
            removePendingFoldersUnder(folderPath);
            await reload();
        } catch (e) {
            setErr((e as Error).message || "folder move failed");
        } finally {
            endOp();
        }
    }, [files, scope, reload]);

    const moveKeys = useCallback(async (keys: string[], destFolder: string) => {
        const label = destFolder ? `${destFolder}/` : "root /";
        if (!beginOp(`Moving 0/${keys.length} to ${label}…`)) return;
        try {
            if (destFolder === "") {
                // Move-to-root: the move endpoint requires a non-empty
                // folder, so root moves are per-key renames to the
                // basename.
                let done = 0;
                for (const k of keys) {
                    updateOp(`Moving ${done + 1}/${keys.length} to ${label}…`);
                    await viewerApi.adminRenameKey(scope, k, basenameOf(k));
                    done++;
                }
            } else {
                const failed: Array<{key: string; reason: string}> = [];
                for (let i = 0; i < keys.length; i += OP_CHUNK) {
                    const chunk = keys.slice(i, i + OP_CHUNK);
                    updateOp(`Moving ${Math.min(i + chunk.length, keys.length)}/${keys.length} to ${label}…`);
                    const r = await viewerApi.adminMoveKeysToFolder(scope, chunk, destFolder);
                    failed.push(...r.failed);
                }
                alertFailures(failed);
            }
            clearSelection();
            await reload();
        } catch (e) {
            setErr((e as Error).message || "move failed");
        } finally {
            endOp();
        }
    }, [scope, reload]);

    // Folder subtree lands at ``destFolder``/``basename`` ("" = root).
    const moveFolderTo = (path: string, destFolder: string) => {
        const base = basenameOf(path);
        const newPath = destFolder ? `${destFolder}/${base}` : base;
        if (!folderHasKeys(path)) {
            rekeyPendingFolders(path, newPath);
            return;
        }
        void runFolderMove(path, newPath);
    };

    // Shared by the bulk toolbar and the Delete key: confirm with an
    // overview of exactly what goes, then delete sequentially.
    const deleteKeysWithConfirm = useCallback(async (keys: string[]) => {
        if (keys.length === 0) return;
        if (!confirm(
            `Delete ${keys.length} file${keys.length === 1 ? "" : "s"} from ${corpus.slug}? ` +
            "This can't be undone.\n\n" +
            previewKeyList(keys),
        )) return;
        if (!beginOp(`Deleting 0/${keys.length}…`)) return;
        try {
            let done = 0;
            for (const k of keys) {
                updateOp(`Deleting ${done + 1}/${keys.length}…`);
                await viewerApi.adminDeleteBlob(scope, k);
                done++;
            }
            setSelected(new Set());
            await reload();
        } catch (e) {
            setErr((e as Error).message || "delete failed");
        } finally {
            endOp();
        }
    }, [scope, corpus.slug, reload]);

    // Server-side copy (Garage CopyObject) corpus → the caller's
    // personal scope, preserving keys. Existing keys are skipped, not
    // overwritten — same semantics as the copy-into-corpus modal.
    const copyToPersonal = useCallback(async (keys: string[]) => {
        if (keys.length === 0) return;
        if (!beginOp(`Copying 0/${keys.length} to your files…`)) return;
        setNote(null);
        try {
            let copied = 0;
            let skipped = 0;
            const failed: Array<{key: string; reason: string}> = [];
            for (let i = 0; i < keys.length; i += OP_CHUNK) {
                const chunk = keys.slice(i, i + OP_CHUNK);
                updateOp(`Copying ${Math.min(i + chunk.length, keys.length)}/${keys.length} to your files…`);
                const r = await viewerApi.adminCopyKeysFromScope("user:me", scope, chunk);
                copied += r.copied.length;
                skipped += r.skipped.length;
                failed.push(...r.failed);
            }
            alertFailures(failed);
            setNote(
                `copied ${copied} to your files` +
                (skipped > 0 ? ` · skipped ${skipped} (already there)` : ""),
            );
        } catch (e) {
            setErr((e as Error).message || "copy to personal scope failed");
        } finally {
            endOp();
        }
    }, [scope]);

    const mutations: FileTreeMutations = {
        renameFile: (key, newName) => {
            const dir = dirnameOf(key);
            const newKey = dir ? `${dir}/${newName}` : newName;
            void (async () => {
                try {
                    await viewerApi.adminRenameKey(scope, key, newKey);
                    await reload();
                } catch (e) {
                    setErr((e as Error).message || "rename failed");
                }
            })();
        },
        renameFolder: (path, newName) => {
            const parent = dirnameOf(path);
            const newPath = parent ? `${parent}/${newName}` : newName;
            if (!folderHasKeys(path)) {
                rekeyPendingFolders(path, newPath);
                return;
            }
            void runFolderMove(path, newPath);
        },
        moveKeys: (keys, destFolder) => void moveKeys(keys, destFolder),
        moveFolder: moveFolderTo,
        deleteFile: (key) => void onDelete(key),
        deleteFolder: (path, fileCount) => {
            if (fileCount === 0) {
                // Pending (empty) folder — pure client state.
                removePendingFoldersUnder(path);
                return;
            }
            const prefix = path + "/";
            const targets = files.filter((f) => normKey(f.key).startsWith(prefix));
            if (!confirm(
                `Delete folder "${path}" and its ${fileCount} file${fileCount === 1 ? "" : "s"} ` +
                `from ${corpus.slug}? This can't be undone.\n\n` +
                previewKeyList(targets.map((t) => t.key)),
            )) return;
            void (async () => {
                if (!beginOp(`Deleting 0/${targets.length} from "${path}"…`)) return;
                try {
                    // Sequential: deletes cascade derived blobs server-side
                    // and parallel calls would race on the storage listing.
                    let done = 0;
                    for (const t of targets) {
                        updateOp(`Deleting ${done + 1}/${targets.length} from "${path}"…`);
                        await viewerApi.adminDeleteBlob(scope, t.key);
                        done++;
                    }
                    removePendingFoldersUnder(path);
                    await reload();
                } catch (e) {
                    setErr((e as Error).message || "folder delete failed");
                } finally {
                    endOp();
                }
            })();
        },
        createFolder: (parent, name) => {
            // ``_derived`` is where the converter parks derived blobs —
            // a user folder with that name would collide with the cache
            // prefix.
            if (!parent && name === "_derived") {
                window.alert(`"${name}" is a reserved name`);
                return;
            }
            const path = parent ? `${parent}/${name}` : name;
            setPendingFolders((prev) => (prev.includes(path) ? prev : [...prev, path]));
        },
        requestMoveFile: (key) => setPicker({
            title: `Move "${key}" to folder`,
            onPick: (folder) => void moveKeys([key], folder),
        }),
        requestMoveFolder: (path) => setPicker({
            title: `Move folder "${path}" into`,
            onPick: (dest) => moveFolderTo(path, dest),
        }),
        deleteKeys: (keys) => void deleteKeysWithConfirm(keys),
        uploadTo: (folder, list) => void uploadFilesTo(list, folder || undefined),
        downloadFile: (key) => void viewerApi.downloadBlob(scope, key, basenameOf(key)),
    };

    const onMoveSelected = () => {
        const keys = Array.from(selected);
        if (keys.length === 0) return;
        setPicker({
            title: `Move ${keys.length} file${keys.length === 1 ? "" : "s"} to folder`,
            onPick: (folder) => void moveKeys(keys, folder),
        });
    };

    const showTree = viewMode === "tree" &&
        (files.length > 0 || pendingFolders.length > 0 || newFolderAt !== null);

    return {
        scope,
        files,
        err,
        note,
        busy,
        uploading,
        progress,
        copyOpen,
        setCopyOpen,
        viewMode,
        setViewMode,
        inputRef,
        reload,
        pendingFolders,
        editingMeta,
        setEditingMeta,
        metaName,
        setMetaName,
        metaDesc,
        setMetaDesc,
        metaBusy,
        openMetaEdit,
        saveMeta,
        newFolderAt,
        setNewFolderAt,
        picker,
        setPicker,
        selected,
        setSelection,
        clearSelection,
        existingFolderPaths,
        uploadFailures,
        setUploadFailures,
        uploadFilesTo,
        onPickUpload,
        onDelete,
        deleteKeysWithConfirm,
        copyToPersonal,
        mutations,
        onMoveSelected,
        showTree,
    };
}
