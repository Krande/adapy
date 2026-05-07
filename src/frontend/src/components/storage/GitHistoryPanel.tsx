import React, {useEffect, useMemo, useState} from "react";
import {ServerFileEntry} from "@/state/serverInfoStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";

// Modal-style panel showing CI uploads as a chronological commit
// timeline, fetched from the per-artefact ``.build.json`` sidecars
// the ada-build pipeline writes alongside each blob.
//
// Currently a flat timeline (no branch lanes / DAG) — the v0 use case
// is "show me the latest run on each branch and let me pick one".
// Lane-style rendering with parent edges is a follow-up; the data
// model below already carries ``parents`` so layering it on top is
// straightforward.

export interface GitProvenance {
    commit: string;
    parents: string[];
    branch: string;
    author: string;
    timestamp: string;     // ISO-8601
    remote_url: string;
    is_dirty: boolean;
}

export interface BuildSidecar {
    schema_version: number;
    project_id: string;
    entrypoint: string;
    artefact: string;      // basename of the GLB
    git: GitProvenance;
}

// One row in the rendered timeline. Aggregated across artefacts when
// a single commit produced multiple files (one row per commit, with
// all artefacts as inline pills).
interface TimelineRow {
    commitKey: string;            // ``<encodedBranch>/<commit>``
    encodedBranch: string;
    branch: string;
    commit: string;
    parents: string[];
    author: string;
    timestamp: string;
    timestampMs: number;
    artefacts: ServerFileEntry[]; // entries under this <branch>/<commit>/
    sidecar: BuildSidecar | null; // null when the sidecar fetch failed
}

interface Props {
    /** All file entries the storage browser knows about — this
     * component picks out the ``versions/<branch>/<commit>/...``
     * subset and pairs each commit with its sidecar. */
    files: ServerFileEntry[];
    /** Names of files currently in the scene; controls "loaded" pill state. */
    loadedSourceNames: ReadonlySet<string>;
    /** True when an overlay/unload is currently in flight; pills
     * disable while busy to mirror the FileRow checkbox semantics. */
    busyName: string | null;
    /** Toggle a single artefact in/out of the scene — wired to the
     * same handler the storage list uses. */
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    onClose: () => void;
}

function shortSha(sha: string): string {
    return sha.length > 8 ? sha.slice(0, 8) : sha;
}

function relTime(iso: string): string {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return iso;
    const dt = (Date.now() - t) / 1000;
    if (dt < 60) return "just now";
    if (dt < 3600) return `${Math.round(dt / 60)} min ago`;
    if (dt < 86400) return `${Math.round(dt / 3600)} h ago`;
    if (dt < 7 * 86400) return `${Math.round(dt / 86400)} d ago`;
    return new Date(t).toISOString().slice(0, 10);
}

// Deterministic branch → Tailwind chip palette. Same branch always
// gets the same colour across reopens; different branches get
// different ones. Cheap string hash → 1-of-N indices.
const BRANCH_PALETTE = [
    "bg-emerald-700",
    "bg-sky-700",
    "bg-violet-700",
    "bg-amber-700",
    "bg-rose-700",
    "bg-teal-700",
    "bg-indigo-700",
];
function branchChipClass(branch: string): string {
    let h = 0;
    for (let i = 0; i < branch.length; i++) {
        h = (h * 31 + branch.charCodeAt(i)) | 0;
    }
    return BRANCH_PALETTE[Math.abs(h) % BRANCH_PALETTE.length];
}

// Group files into per-commit buckets and synthesise stub rows. The
// sidecar fetch fills in branch/parents/timestamp/author after the
// fact.
function buildSkeleton(files: ServerFileEntry[]): TimelineRow[] {
    const groups = new Map<string, TimelineRow>();
    for (const f of files) {
        const trimmed = f.name.replace(/^\/+/, "");
        const parts = trimmed.split("/");
        if (parts.length < 4 || parts[0] !== "versions") continue;
        const [, encodedBranch, sha, ...rest] = parts;
        const artefactName = rest.join("/");
        if (artefactName.endsWith(".build.json")) continue;
        const key = `${encodedBranch}/${sha}`;
        let row = groups.get(key);
        if (!row) {
            row = {
                commitKey: key,
                encodedBranch,
                branch: encodedBranch.replace(/__/g, "/"),
                commit: sha,
                parents: [],
                author: "",
                timestamp: "",
                timestampMs: 0,
                artefacts: [],
                sidecar: null,
            };
            groups.set(key, row);
        }
        row.artefacts.push(f);
    }
    return Array.from(groups.values());
}

const GitHistoryPanel: React.FC<Props> = ({
    files,
    loadedSourceNames,
    busyName,
    onToggle,
    onClose,
}) => {
    const skeleton = useMemo(() => buildSkeleton(files), [files]);
    const [rows, setRows] = useState<TimelineRow[]>(skeleton);
    const [pending, setPending] = useState<number>(skeleton.length);
    const [errors, setErrors] = useState<number>(0);
    const scope = scopeUrlPart(useScopeStore.getState().current);

    // Esc to close.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    // Fetch sidecars on mount. Skeleton rows already render
    // immediately (you can still see commit SHAs) and get filled
    // in as fetches resolve. Each commit picks the first artefact's
    // sidecar (they all carry the same git provenance).
    useEffect(() => {
        let cancelled = false;
        const next: TimelineRow[] = skeleton.map((r) => ({...r}));
        let remaining = next.length;
        if (remaining === 0) {
            setPending(0);
            return;
        }
        let errCount = 0;
        const finalize = (i: number, sidecar: BuildSidecar | null) => {
            if (cancelled) return;
            if (sidecar) {
                next[i].sidecar = sidecar;
                next[i].branch = sidecar.git.branch || next[i].branch;
                next[i].parents = sidecar.git.parents ?? [];
                next[i].author = sidecar.git.author ?? "";
                next[i].timestamp = sidecar.git.timestamp ?? "";
                next[i].timestampMs = sidecar.git.timestamp
                    ? Date.parse(sidecar.git.timestamp) || 0
                    : 0;
            } else {
                errCount += 1;
            }
            remaining -= 1;
            if (remaining === 0) {
                // Sort newest-first by sidecar timestamp; rows with
                // missing sidecars sink to the bottom but keep their
                // SHA for manual identification.
                next.sort((a, b) => b.timestampMs - a.timestampMs);
                setRows(next);
                setPending(0);
                setErrors(errCount);
            }
        };
        for (let i = 0; i < next.length; i++) {
            const row = next[i];
            const sidecarKey = `versions/${row.encodedBranch}/${row.commit}/${row.artefacts[0]?.name.split("/").pop() ?? ""}.build.json`;
            // The artefact name itself already contains the
            // ``.glb`` suffix; concatenating ``.build.json`` matches
            // the upload convention in ada-build's _upload.py.
            (async () => {
                try {
                    const buf = await viewerApi.getBlob(scope, sidecarKey);
                    const text = new TextDecoder().decode(buf);
                    finalize(i, JSON.parse(text) as BuildSidecar);
                } catch (err) {
                    console.warn(`sidecar fetch failed for ${sidecarKey}:`, err);
                    finalize(i, null);
                }
            })();
        }
        return () => {
            cancelled = true;
        };
    }, [skeleton, scope]);

    return (
        <div
            className="fixed inset-0 z-[60] flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
            onClick={onClose}
        >
            <div
                className="bg-gray-900 border border-gray-700 rounded shadow-xl flex flex-col max-w-3xl w-full max-h-[calc(100dvh-2rem)] sm:max-h-[85dvh] my-auto"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-label="Git history"
            >
                <div className="flex items-start gap-3 border-b border-gray-700 px-4 py-2">
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold">Git history</div>
                        <div className="text-xs text-gray-400">
                            {pending > 0
                                ? `Fetching ${pending} sidecar${pending === 1 ? "" : "s"}…`
                                : `${rows.length} commit${rows.length === 1 ? "" : "s"}` +
                                  (errors > 0
                                      ? ` (${errors} sidecar${errors === 1 ? "" : "s"} unavailable)`
                                      : "")}
                        </div>
                    </div>
                    <button
                        type="button"
                        className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                        onClick={onClose}
                        aria-label="Close"
                        title="Close (Esc)"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-auto px-3 py-2">
                    {rows.length === 0 ? (
                        <div className="text-xs italic text-gray-300 px-1 py-4 text-center">
                            No CI uploads under <code>versions/</code> in this scope yet.
                        </div>
                    ) : (
                        <ul className="flex flex-col gap-1">
                            {rows.map((r, idx) => (
                                <CommitRow
                                    key={r.commitKey}
                                    row={r}
                                    isFirstOnBranch={
                                        // First (most recent) row on this branch in the
                                        // sorted list — earns the "latest" badge.
                                        rows.findIndex((x) => x.branch === r.branch) === idx
                                    }
                                    loadedSourceNames={loadedSourceNames}
                                    busyName={busyName}
                                    onToggle={onToggle}
                                />
                            ))}
                        </ul>
                    )}
                </div>
            </div>
        </div>
    );
};

interface CommitRowProps {
    row: TimelineRow;
    isFirstOnBranch: boolean;
    loadedSourceNames: ReadonlySet<string>;
    busyName: string | null;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
}

const CommitRow: React.FC<CommitRowProps> = ({
    row,
    isFirstOnBranch,
    loadedSourceNames,
    busyName,
    onToggle,
}) => {
    return (
        <li className="border border-gray-700/60 rounded bg-gray-800/40 hover:bg-gray-800/70 transition-colors">
            <div className="flex items-center gap-2 px-2 py-2 flex-wrap">
                <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono text-white ${branchChipClass(row.branch)}`}
                    title={row.branch}
                >
                    {row.branch}
                </span>
                <span className="font-mono text-[11px] text-gray-200" title={row.commit}>
                    {shortSha(row.commit)}
                </span>
                {isFirstOnBranch && (
                    <span
                        className="px-1 py-0 rounded text-[9px] uppercase tracking-wide bg-emerald-700 text-white"
                        title="Most recent commit on this branch"
                    >
                        latest
                    </span>
                )}
                <span
                    className="text-[10px] text-gray-300 ml-auto whitespace-nowrap"
                    title={row.timestamp || "(timestamp unavailable)"}
                >
                    {relTime(row.timestamp)}
                </span>
            </div>
            {(row.author || row.parents.length > 0) && (
                <div className="px-2 pb-1 text-[10px] text-gray-400 flex items-center gap-2 flex-wrap">
                    {row.author && <span title={`Author: ${row.author}`}>{row.author}</span>}
                    {row.parents.length > 0 && (
                        <span className="font-mono" title={`Parents: ${row.parents.join(", ")}`}>
                            ← {row.parents.map(shortSha).join(", ")}
                        </span>
                    )}
                </div>
            )}
            {row.artefacts.length > 0 && (
                <div className="px-2 pb-2 flex flex-wrap gap-1">
                    {row.artefacts.map((a) => {
                        const isLoaded = loadedSourceNames.has(a.name);
                        const isBusy = busyName === a.name;
                        const otherBusy = busyName !== null && !isBusy;
                        const artefactName = a.name.split("/").pop() ?? a.name;
                        return (
                            <button
                                key={a.name}
                                type="button"
                                onClick={() => void onToggle(a, !isLoaded)}
                                disabled={isBusy || otherBusy}
                                className={
                                    "px-2 py-1 rounded text-[10px] font-mono whitespace-nowrap " +
                                    "disabled:opacity-50 disabled:cursor-not-allowed " +
                                    (isLoaded
                                        ? "bg-blue-700 hover:bg-blue-600 text-white"
                                        : "bg-gray-700 hover:bg-gray-600 text-gray-100")
                                }
                                title={
                                    isLoaded
                                        ? "Loaded — click to remove from scene"
                                        : "Click to add to scene"
                                }
                            >
                                {isLoaded ? "✓ " : ""}{artefactName}
                            </button>
                        );
                    })}
                </div>
            )}
        </li>
    );
};

export default GitHistoryPanel;
