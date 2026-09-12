import {useEffect, useState} from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import {
    loadExpandedFolders,
    loadPendingFolders,
    saveExpandedFolders,
    savePendingFolders,
} from "@/utils/storage/fileTree";

// Folder expand state, client-side pending (empty) folders and the inline
// "new folder" input position — all per scope.
export function useFolderTree(scopeKey: string, files: ServerFileEntry[]) {
    // Folder expand state for the regular-files tree, keyed by folder
    // path ("a/b/c"). Default: empty Set = everything collapsed,
    // matching the user-requested behaviour. Persisted per-scope so
    // expand state survives reloads but doesn't leak across scopes.
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

    return {
        expandedFolders,
        setExpandedFolders,
        toggleFolder,
        pendingFolders,
        setPendingFolders,
        removePendingFoldersUnder,
        newFolderAt,
        setNewFolderAt,
    };
}
