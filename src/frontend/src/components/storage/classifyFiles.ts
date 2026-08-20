import type {ServerFileEntry} from "@/state/serverInfoStore";
import type {BuildSidecar} from "@/hooks/useBuildSidecars";
import {parseLastModifiedMs} from "./storageHelpers";

// Splitting a flat key list into "ordinary files" and "CI version artefacts", and
// grouping the latter by branch and commit.
//
// This is the one piece of real logic in the storage browser — everything else is
// presentation over a server response. Pure and separate so it is tested rather than
// eyeballed: the sort rule below in particular is easy to get subtly wrong and hard to
// notice, because a wrong "latest" still looks like a plausible commit.

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

