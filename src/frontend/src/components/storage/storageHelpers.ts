import type {ServerFileEntry} from "@/state/serverInfoStore";
import type {FileTreeNode, FolderNode} from "@/utils/storage/fileTree";

// Pure helpers lifted out of StorageBrowser: key/path arithmetic, byte and date
// formatting, and the drag MIME types.
//
// Split out so they are testable under plain `node --test` — StorageBrowser itself
// reaches stores that reach the model worker (`?worker&inline`), which the bundler
// resolves and the test runner cannot. Same reason coreProviderRules, commandFilter and
// gizmoRules are separate from their wiring.

/** Drag payload for one or more file keys. */
export const KEYS_MIME = "application/x-adapy-keys";
// Folder drags carry the folder path instead — the drop handler moves the whole prefix
// (subfolders preserved via the grouped-move helper).
export const FOLDER_MIME = "application/x-adapy-folder";

export function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function dirnameOf(key: string): string {
    const i = key.lastIndexOf("/");
    return i >= 0 ? key.slice(0, i) : "";
}

export function basenameOf(key: string): string {
    return key.split("/").pop() ?? key;
}

// CI uploads land at ``versions/<branch>/<commit>/<filename>``; the
// helpers below split the storage list into "regular" files (treated
// as before) and a tree grouped by branch + commit so the storage
// browser can show a collapsible per-branch history with the latest
// commit pinned.

export function parseLastModifiedMs(iso: string): number {
    if (!iso) return 0;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : 0;
}


export function shortSha(sha: string): string {
    return sha.length > 8 ? sha.slice(0, 8) : sha;
}

// File-tree shape comes from `@/utils/storage/fileTree`; here we just specialise the
// generic to `ServerFileEntry` so existing call sites read the same as before. The admin
// StorageTab uses the same helpers with its own entry type.
export type ServerFileTreeNode = FileTreeNode<ServerFileEntry>;
export type ServerFolderNode = FolderNode<ServerFileEntry>;

export function countFiles(node: ServerFileTreeNode): number {
    if (node.kind === "file") return 1;
    return node.children.reduce((acc, c) => acc + countFiles(c), 0);
}

export function formatRelative(iso: string): string {
    const t = parseLastModifiedMs(iso);
    if (t === 0) return "";
    const dt = (Date.now() - t) / 1000;
    if (dt < 60) return "just now";
    if (dt < 3600) return `${Math.round(dt / 60)} min ago`;
    if (dt < 86400) return `${Math.round(dt / 3600)} h ago`;
    if (dt < 7 * 86400) return `${Math.round(dt / 86400)} d ago`;
    return new Date(t).toISOString().slice(0, 10);
}

