import React, {useRef, useState} from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import {viewerApi} from "@/services/viewerApi";
import {previewKeyList} from "@/utils/storage/fileTree";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";
import type {useStorageMutations} from "../useStorageMutations";
import {basenameOf, dirnameOf} from "./helpers";
import type {FolderPicker} from "./useUploads";

// Single-file / single-folder mutations: move, rename, delete, create folder,
// download — through the scope-appropriate endpoints (useStorageMutations),
// with the in-flight status line for chunked moves.
export function useFileOps(p: {
    files: ServerFileEntry[];
    scopeKey: string;
    mutations: ReturnType<typeof useStorageMutations>;
    loadedSourceNames: ReadonlySet<string>;
    setExpandedFolders: React.Dispatch<React.SetStateAction<Set<string>>>;
    setPendingFolders: React.Dispatch<React.SetStateAction<string[]>>;
    removePendingFoldersUnder: (path: string) => void;
    clearSelection: () => void;
    setPicker: (picker: FolderPicker | null) => void;
    setNewFolderAt: (at: string | null) => void;
}) {
    const {
        files, scopeKey, mutations, loadedSourceNames, setExpandedFolders, setPendingFolders,
        removePendingFoldersUnder, clearSelection, setPicker, setNewFolderAt,
    } = p;
    // Inline rename target (replaces the old window.prompt flow).
    const [renaming, setRenaming] = useState<{kind: "file" | "folder"; path: string} | null>(null);

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

    return {
        renaming,
        setRenaming,
        onDownloadFile,
        alertError,
        opNote,
        moveKeysWithProgress,
        onMoveSingleToFolder,
        runFolderMove,
        onMoveFolderInto,
        onRenameFolderCommit,
        onRenameFileCommit,
        unloadIfLoaded,
        onDeleteFile,
        onDeleteFolder,
        onCreateFolder,
    };
}
