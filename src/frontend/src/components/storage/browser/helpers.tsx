import React from "react";
import {localDate} from "@/utils/time";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import type {BuildSidecar} from "@/hooks/useBuildSidecars";
import type {FileTreeNode, FolderNode} from "@/utils/storage/fileTree";

// Helpers shared by the storage browser's pieces: drag MIME types, a small
// spinner, key helpers, and the CI ``versions/<branch>/<commit>/…`` grouping.

// Custom drag MIME for in-panel file moves. OS-file drops arrive as
// ``dataTransfer.files`` instead; checking for this type tells the two
// apart (types are readable during dragover, the payload only on drop).
export const KEYS_MIME = "application/x-adapy-keys";
// Folder drags carry the folder path instead — the drop handler moves
// the whole prefix (subfolders preserved via the grouped-move helper).
export const FOLDER_MIME = "application/x-adapy-folder";

// Small inline CSS spinner. Uses border tricks rather than an SVG so
// it scales with text size and stays crisp at 16px tall icons.
export const Spinner: React.FC<{className?: string}> = ({className = ""}) => (
    <span
        className={`inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin ${className}`}
        aria-hidden="true"
    />
);

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

export interface VersionLeaf {
    file: ServerFileEntry;
    artefactName: string;       // basename — last segment of the key
}

export interface CommitGroup {
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

export interface BranchGroup {
    encodedBranch: string;      // path-safe form (slashes replaced with __)
    displayBranch: string;      // human-friendly (slashes restored)
    commits: CommitGroup[];     // sorted newest-first by sortKey
    sortKey: number;            // max across commits
}

export function parseLastModifiedMs(iso: string): number {
    if (!iso) return 0;
    const t = Date.parse(iso);
    return Number.isFinite(t) ? t : 0;
}

export function classifyFiles(
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

export function shortSha(sha: string): string {
    return sha.length > 8 ? sha.slice(0, 8) : sha;
}

// File-tree shape comes from ``@/utils/storage/fileTree``; here we
// just specialise the generic to ``ServerFileEntry`` so existing call
// sites read the same as before. The admin StorageTab uses the same
// helpers with its own entry type.
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
    return localDate(t);
}
