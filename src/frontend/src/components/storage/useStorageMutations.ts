import {useMemo} from "react";
import {viewerApi, MoveKeysResult, MovedKeyEntry} from "@/services/viewerApi";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {useMeStore} from "@/state/meStore";

// Single predicate + dispatch point for every mutating storage action
// in the user-facing panel. Personal scope → everyone, via the
// user-level endpoints; any other scope → admins only, via the admin
// endpoints (the backend enforces the same split). When canMutate is
// false the UI hides rename/move/delete affordances entirely.
export interface StorageMutations {
    canMutate: boolean;
    deleteKey: (key: string) => Promise<{deleted: string[]; errors?: string[]}>;
    moveKeys: (keys: string[], folder: string) => Promise<MoveKeysResult>;
    renameKey: (oldKey: string, newKey: string) => Promise<MovedKeyEntry>;
    renameOrMoveFolder: (
        oldFolder: string,
        newFolder: string,
        allKeys: string[],
    ) => Promise<MoveKeysResult>;
}

export function useStorageMutations(): StorageMutations {
    const scope = useScopeStore((s) => s.current);
    const isAdmin = useMeStore((s) => s.isAdmin);

    return useMemo(() => {
        const scopeUrl = scopeUrlPart(scope);
        const personal = scope?.kind === "user";
        const canMutate = personal || isAdmin;

        if (personal) {
            return {
                canMutate,
                deleteKey: (key) => viewerApi.deleteBlob(scopeUrl, key),
                moveKeys: (keys, folder) => viewerApi.moveKeysToFolder(scopeUrl, keys, folder),
                renameKey: (oldKey, newKey) => viewerApi.renameKey(scopeUrl, oldKey, newKey),
                renameOrMoveFolder: (oldFolder, newFolder, allKeys) =>
                    viewerApi.renameOrMoveFolder(scopeUrl, oldFolder, newFolder, allKeys),
            };
        }
        return {
            canMutate,
            deleteKey: (key) => viewerApi.adminDeleteBlob(scopeUrl, key),
            moveKeys: (keys, folder) => viewerApi.adminMoveKeysToFolder(scopeUrl, keys, folder),
            renameKey: (oldKey, newKey) => viewerApi.adminRenameKey(scopeUrl, oldKey, newKey),
            renameOrMoveFolder: (oldFolder, newFolder, allKeys) =>
                viewerApi.adminRenameOrMoveFolder(scopeUrl, oldFolder, newFolder, allKeys),
        };
    }, [scope, isAdmin]);
}
