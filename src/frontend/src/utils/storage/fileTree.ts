// Shared folder-tree builder + collapse-state persistence used by both
// the regular ``StorageBrowser`` panel and the admin ``StorageTab``.
// Storage stays flat on the server; folders are presentational only
// (option 1 from the original design discussion). A key's last segment
// is the filename; everything before is the folder path.
//
// Generic over the file entry type so the regular browser
// (``ServerFileEntry`` with ``name``) and the admin storage table
// (``AdminFileEntry`` with ``key``) share one implementation. Pass a
// ``getPath`` extractor at the call site.

export type FolderNode<T> = {
    kind: "folder";
    /** Single segment, e.g. ``"fea-examples"``. */
    name: string;
    /** Full path from root, e.g. ``"a/b/c"``. */
    path: string;
    children: FileTreeNode<T>[];
};

export type FileNode<T> = {
    kind: "file";
    file: T;
    /** Filename only, with parent prefix stripped. */
    displayName: string;
};

export type FileTreeNode<T> = FolderNode<T> | FileNode<T>;

export function buildFileTree<T>(
    files: T[],
    getPath: (file: T) => string,
): FileTreeNode<T>[] {
    const root: FolderNode<T> = {
        kind: "folder",
        name: "",
        path: "",
        children: [],
    };
    // path → folder node, for O(1) lookup as we descend.
    const folderIndex = new Map<string, FolderNode<T>>();
    folderIndex.set("", root);

    for (const f of files) {
        const trimmed = getPath(f).replace(/^\/+/, "");
        const parts = trimmed.split("/");
        const filename = parts.pop() ?? trimmed;
        let parent = root;
        let acc = "";
        for (const seg of parts) {
            acc = acc ? `${acc}/${seg}` : seg;
            let next = folderIndex.get(acc);
            if (!next) {
                next = {kind: "folder", name: seg, path: acc, children: []};
                folderIndex.set(acc, next);
                parent.children.push(next);
            }
            parent = next;
        }
        parent.children.push({
            kind: "file",
            file: f,
            displayName: filename,
        });
    }

    // Folders first (alpha), then files (alpha by display name). Stable
    // order is preferable to mtime-order here; files within a folder
    // are usually a small set the user wants to scan visually.
    const sortNode = (n: FolderNode<T>) => {
        n.children.sort((a, b) => {
            if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
            const an = a.kind === "folder" ? a.name : a.displayName;
            const bn = b.kind === "folder" ? b.name : b.displayName;
            return an.localeCompare(bn);
        });
        for (const c of n.children) {
            if (c.kind === "folder") sortNode(c);
        }
    };
    sortNode(root);

    return root.children;
}

// Persisted per-scope so expand state survives reloads but doesn't
// leak across scopes. Caller passes a ``namespace`` (e.g. "storage"
// for the regular browser, "admin-storage" for the admin table) so
// the two panels don't fight over the same key.

function expandedFoldersKey(namespace: string, scope: string): string {
    return `ada.${namespace}.expandedFolders.${scope}`;
}

export function loadExpandedFolders(
    namespace: string,
    scope: string,
): Set<string> {
    try {
        const raw = window.localStorage.getItem(
            expandedFoldersKey(namespace, scope),
        );
        if (!raw) return new Set();
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
            return new Set(parsed.filter((x) => typeof x === "string"));
        }
    } catch {
        // corrupt entry — fall back to fully collapsed.
    }
    return new Set();
}

// Collect every folder path that appears in the keys — useful for
// "Move to folder…" pickers that need to offer the existing folders
// as a dropdown. Returns sorted, deduplicated paths *without* the
// empty root (a "move" to "" is meaningless; the caller can prepend
// a "(root)" option if they want one). Includes intermediate parents
// (e.g. ``a`` is included even if only ``a/b/c.ifc`` exists).
export function collectFolderPaths<T>(
    files: T[],
    getPath: (file: T) => string,
): string[] {
    const seen = new Set<string>();
    for (const f of files) {
        const trimmed = getPath(f).replace(/^\/+/, "");
        const parts = trimmed.split("/");
        parts.pop();
        let acc = "";
        for (const seg of parts) {
            acc = acc ? `${acc}/${seg}` : seg;
            seen.add(acc);
        }
    }
    return Array.from(seen).sort((a, b) => a.localeCompare(b));
}

export function saveExpandedFolders(
    namespace: string,
    scope: string,
    expanded: ReadonlySet<string>,
): void {
    try {
        window.localStorage.setItem(
            expandedFoldersKey(namespace, scope),
            JSON.stringify(Array.from(expanded)),
        );
    } catch {
        // localStorage full / disabled — silently lose the state.
    }
}
