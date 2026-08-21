import React, {useEffect, useRef, useState} from "react";
import {ServerFileEntry} from "@/state/serverInfoStore";
import {BuildSidecar} from "@/hooks/useBuildSidecars";
import {KebabMenuItem} from "@/components/common/PositionedMenu";
import {formatRelative, shortSha} from "./storageHelpers";
import {FileRow} from "./FileRow";
import {BranchGroup} from "./classifyFiles";


// Custom drag MIME for in-panel file moves. OS-file drops arrive as
// ``dataTransfer.files`` instead; checking for this type tells the two
// apart (types are readable during dragover, the payload only on drop).
// The CI-artefact side of the browser: a collapsible per-branch, per-commit tree built
// from classifyFiles. Moved verbatim out of StorageBrowser.
export interface VersionsTreeProps {
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
    selection: Set<string>;
    onSelectToggle: (name: string) => void;
    /** Read-only menu builder from the parent (load/streamer/download). */
    fileMenuItemsFor: (file: ServerFileEntry) => KebabMenuItem[];
    onOpenContextMenu: (
        e: {clientX: number; clientY: number; preventDefault?: () => void; stopPropagation?: () => void},
        items: KebabMenuItem[],
    ) => void;
    showModified?: boolean;
}

export const VersionsTree: React.FC<VersionsTreeProps> = (props) => {
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
        <div className="border-t border-edge pt-1 mt-1">
            <div className="flex items-center justify-between px-1 pb-1">
                <div className="text-[10px] uppercase tracking-wide text-content-muted">
                    Versions
                </div>
                <button
                    type="button"
                    onClick={props.onOpenGitHistory}
                    className="text-[10px] px-1.5 py-0.5 rounded-sm bg-surface-2 pointer-fine:hover:bg-surface-3 text-white"
                    title="Open chronological commit timeline with author + parent links"
                >
                    Git history
                </button>
            </div>
            <ul className="flex flex-col divide-y divide-edge">
                {branches.map((b, bIdx) => {
                    const branchOpen = openBranches.has(b.encodedBranch);
                    return (
                        <li key={b.encodedBranch} className="flex flex-col">
                            <button
                                type="button"
                                onClick={() => toggleBranch(b.encodedBranch)}
                                className="flex items-center gap-1 px-1 py-1 text-xs text-left w-full pointer-fine:hover:bg-surface-0"
                                aria-expanded={branchOpen}
                                title={b.displayBranch}
                            >
                                <span className="w-3 inline-block text-content">
                                    {branchOpen ? "▾" : "▸"}
                                </span>
                                <span className="font-mono text-[11px] truncate flex-1 min-w-0">
                                    {b.displayBranch}
                                </span>
                                <span className="text-[10px] text-content-muted shrink-0">
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
                                                    className="flex items-center gap-1 px-1 py-1 text-xs text-left w-full pointer-fine:hover:bg-surface-0"
                                                    style={{paddingLeft: "16px"}}
                                                    aria-expanded={commitOpen}
                                                >
                                                    <span className="w-3 inline-block text-content">
                                                        {commitOpen ? "▾" : "▸"}
                                                    </span>
                                                    <span className="font-mono text-[11px] shrink-0">
                                                        {shortSha(c.sha)}
                                                    </span>
                                                    {isLatest && (
                                                        <span
                                                            className="ml-1 px-1 rounded-sm text-[9px] uppercase tracking-wide bg-pass text-white shrink-0"
                                                            title="Most recent commit on this branch"
                                                        >
                                                            latest
                                                        </span>
                                                    )}
                                                    <span className="ml-auto text-[10px] text-content-muted shrink-0">
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
                                                    <ul className="flex flex-col divide-y divide-edge">
                                                        {c.leaves.map((leaf) => {
                                                            const items = props.fileMenuItemsFor(leaf.file);
                                                            return (
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
                                                                    isSelected={props.selection.has(leaf.file.name)}
                                                                    onSelectToggle={props.onSelectToggle}
                                                                    menuItems={items}
                                                                    onOpenContextMenu={(e) => props.onOpenContextMenu(e, items)}
                                                                    showModified={props.showModified}
                                                                />
                                                            );
                                                        })}
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

