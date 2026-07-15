// Single-file picker over a scope's S3 storage. Reuses the shared FileTreeView (the same folder-tree
// row look as the main StorageBrowser) so picking a compare file uses the storage-browser UI rather
// than a bespoke text field. Read-only: it lists files and returns the chosen key — uploads stay in
// the normal storage browser flow. Portaled to document.body like the other picker modals.

import React from "react";
import {createPortal} from "react-dom";

import {viewerApi, type FileEntry, type ScopeUrl} from "@/services/viewerApi";
import {buildFileTree} from "@/utils/storage/fileTree";
import FileTreeView from "@/components/admin/FileTreeView";

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    const u = ["KB", "MB", "GB", "TB"];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < u.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${u[i]}`;
}

// Per-scope listing cache, module-level so it survives the modal unmounting between opens (the
// modal is mounted per call site and returns null when closed, so component state cannot hold it).
//
// Deliberately NOT inside viewerApi.listFiles: that primitive is also how CorpusTab decides what
// already exists in a destination scope before copying, and a silently-cached answer there would be
// wrong rather than merely stale. Caching is the PICKER's choice, so it stays opt-in.
//
// Entries hold the RAW listing. Filtering happens at render because `filter` is typically an inline
// arrow (a fresh identity every parent render) — caching filtered results would key the cache on
// something that changes constantly, and re-filtering a listing is trivial next to a round-trip.
const _listCache = new Map<string, FileEntry[]>();

/** Drop a scope's cached listing (or all of them). Call after a mutation that this modal cannot
 * see — an upload or delete elsewhere in the app. */
export function invalidateFilePickerCache(scope?: ScopeUrl): void {
    if (scope === undefined) _listCache.clear();
    else _listCache.delete(String(scope));
}

export interface FilePickerModalProps {
    open: boolean;
    scope: ScopeUrl;
    title?: string;
    /** Pre-select this key (the current value). */
    initialKey?: string;
    /** Restrict the listed files (e.g. only ``.glb``). */
    filter?: (f: FileEntry) => boolean;
    onCancel: () => void;
    onPick: (key: string) => void;
}

const FilePickerModal: React.FC<FilePickerModalProps> = ({
    open,
    scope,
    title = "Pick a file from scope",
    initialKey,
    filter,
    onCancel,
    onPick,
}) => {
    const [raw, setRaw] = React.useState<FileEntry[]>(() => _listCache.get(String(scope)) ?? []);
    // "Loading" is now only for a scope we have never listed. A cached scope renders its rows
    // immediately and revalidates behind them, so reopening the picker never blanks the list.
    const [loading, setLoading] = React.useState(false);
    const [refreshing, setRefreshing] = React.useState(false);
    const [picked, setPicked] = React.useState<string | null>(initialKey ?? null);
    const [err, setErr] = React.useState<string | null>(null);

    const load = React.useCallback(
        async (mode: "initial" | "revalidate") => {
            if (mode === "initial") setLoading(true);
            else setRefreshing(true);
            try {
                const fs = await viewerApi.listFiles(scope);
                _listCache.set(String(scope), fs);
                setRaw(fs);
                setErr(null);
            } catch (e) {
                setErr(String(e));
                // Keep whatever we already have: a failed REVALIDATE must not blank a list the user
                // is looking at. Only an initial load has nothing to fall back on.
                if (mode === "initial") setRaw([]);
            } finally {
                setLoading(false);
                setRefreshing(false);
            }
        },
        [scope],
    );

    React.useEffect(() => {
        if (!open) return;
        setPicked(initialKey ?? null);
        const cached = _listCache.get(String(scope));
        if (cached) {
            // Stale-while-revalidate: show the cached rows now, correct them in the background. The
            // listing is cheap; the wait was never the cost, the round-trip's latency was.
            setRaw(cached);
            setErr(null);
            void load("revalidate");
        } else {
            void load("initial");
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, scope]);

    // Filter at render: `filter` is usually an inline arrow, so depending on its identity would
    // refilter on every parent render anyway — and this keeps the cache filter-agnostic.
    const files = React.useMemo(() => (filter ? raw.filter(filter) : raw), [raw, filter]);

    React.useEffect(() => {
        if (!open) return;
        const h = (e: KeyboardEvent) => {
            if (e.key === "Escape") onCancel();
        };
        window.addEventListener("keydown", h);
        return () => window.removeEventListener("keydown", h);
    }, [open, onCancel]);

    if (!open) return null;

    const tree = buildFileTree(files, (f) => f.key);
    const selected = new Set(picked ? [picked] : []);
    // Single-select: a file checkbox sets the pick; a folder checkbox takes its last descendant.
    const onSelect = (keys: string[], select: boolean) => {
        if (!select) {
            setPicked((p) => (p && keys.includes(p) ? null : p));
            return;
        }
        setPicked(keys.length ? keys[keys.length - 1] : null);
    };

    return createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onCancel}>
            <div
                className="bg-gray-900 text-gray-100 border border-gray-700 rounded-md shadow-xl w-[36rem] max-w-[92vw] flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="px-4 py-2 border-b border-gray-700 font-medium flex items-center gap-2">
                    <span className="flex-1">{title}</span>
                    {refreshing && <span className="text-xs font-normal text-gray-500">refreshing…</span>}
                    <button
                        type="button"
                        className="text-xs px-2 py-0.5 rounded-sm bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-50"
                        disabled={loading || refreshing}
                        onClick={() => void load("revalidate")}
                        title="Re-list this scope's files"
                    >
                        Refresh
                    </button>
                </div>
                <div className="px-2 py-2 overflow-auto max-h-[60vh] min-h-[8rem]">
                    {loading ? (
                        <div className="text-xs text-gray-500 px-2 py-4">Loading…</div>
                    ) : files.length === 0 ? (
                        <div className="text-xs text-gray-500 italic px-2 py-4">
                            No files in this scope. Upload comparison files via the storage browser first.
                        </div>
                    ) : (
                        <FileTreeView
                            nodes={tree}
                            getKey={(f) => f.key}
                            namespace="util-ref-pick"
                            scope={String(scope)}
                            selection={{selected, onSelect}}
                            renderFileTail={(f) => (
                                <span className="text-gray-400 font-mono text-xs">{fmtBytes(f.size)}</span>
                            )}
                        />
                    )}
                </div>
                {err && <div className="text-xs text-red-400 px-4 py-1">{err}</div>}
                <div className="px-4 py-2 border-t border-gray-700 flex justify-between items-center gap-2">
                    <span className="text-xs font-mono text-gray-400 truncate flex-1" title={picked ?? ""}>
                        {picked ?? "— nothing selected —"}
                    </span>
                    <button
                        type="button"
                        className="text-sm px-2 py-1 rounded-sm bg-gray-600 text-white"
                        onClick={onCancel}
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        className="text-sm px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50"
                        disabled={!picked}
                        onClick={() => picked && onPick(picked)}
                    >
                        Use file
                    </button>
                </div>
            </div>
        </div>,
        document.body,
    );
};

export default FilePickerModal;
