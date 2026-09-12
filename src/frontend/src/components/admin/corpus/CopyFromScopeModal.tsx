import React, {useCallback, useEffect, useMemo, useState} from "react";
import {FileEntry, viewerApi} from "@/services/viewerApi";
import {buildFileTree} from "@/utils/storage/fileTree";
import {scopeUrlPart} from "@/state/scopeStore";
import {formatBytes} from "@/utils/format";
import {DataTable, DataTableColumn} from "@/components/common/DataTable";
import FileTreeView from "../FileTreeView";
import {ViewMode, ViewModeToggle} from "./shared";

const COPY_TH = "px-3 py-1 border-b border-gray-800";
const COPY_TD = "px-3 py-1 border-b border-gray-800";

// Flat-list columns of the copy modal. The header checkbox toggles every
// shown row; the row checkbox stops propagation so the row's own click
// handler does not toggle it back.
function copyColumns(
    allShownSelected: boolean,
    toggleAll: () => void,
    selected: Set<string>,
    toggle: (key: string) => void,
): DataTableColumn<FileEntry>[] {
    return [
        {
            key: "select",
            header: <input type="checkbox" checked={allShownSelected} onChange={toggleAll}/>,
            headerClassName: COPY_TH + " w-8",
            cellClassName: COPY_TD + " text-center",
            cell: (f) => (
                <input
                    type="checkbox"
                    checked={selected.has(f.key)}
                    onChange={() => toggle(f.key)}
                    onClick={(e) => e.stopPropagation()}
                />
            ),
        },
        {
            key: "key",
            header: "Key",
            headerClassName: "text-left " + COPY_TH + " font-medium text-gray-300",
            cellClassName: "font-mono text-gray-200 " + COPY_TD + " truncate max-w-md",
            cell: (f) => f.key,
        },
        {
            key: "size",
            header: "Size",
            headerClassName: "text-right " + COPY_TH + " font-medium text-gray-300",
            cellClassName: "text-right text-gray-400 " + COPY_TD + " font-mono",
            cell: (f) => formatBytes(f.size),
        },
    ];
}

// Pick files from one of the caller's other scopes (user / shared / project)
// and server-side copy the selection into the corpus. Garage CopyObject — no
// download/reupload — so even large STEP files copy instantly.
const CopyFromScopeModal: React.FC<{
    dstScope: string;
    dstSlug: string;
    onClose: () => void;
    onCopied: () => void;
}> = ({dstScope, dstSlug, onClose, onCopied}) => {
    const [scopes, setScopes] = useState<Array<{name: string; url: string}>>([]);
    const [srcScope, setSrcScope] = useState("");
    const [files, setFiles] = useState<FileEntry[]>([]);
    const [loadingFiles, setLoadingFiles] = useState(false);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [filter, setFilter] = useState("");
    const [viewMode, setViewMode] = useState<ViewMode>("flat");
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [result, setResult] = useState<{copied: number; skipped: number; failed: {key: string; reason: string}[]} | null>(null);
    // Destination corpus keys, fetched once — files already in the corpus
    // render greyed "in corpus" and can't be (re)selected; the backend also
    // skips them, so this is purely a clearer-affordance pre-check.
    const [existingKeys, setExistingKeys] = useState<ReadonlySet<string>>(new Set());

    useEffect(() => {
        void (async () => {
            try {
                const xs = await viewerApi.listFiles(dstScope);
                setExistingKeys(new Set(xs.map((f) => f.key)));
            } catch {
                // Best-effort — without it nothing is greyed, but the copy
                // still skips collisions server-side.
            }
        })();
    }, [dstScope]);

    useEffect(() => {
        void (async () => {
            try {
                const me = await viewerApi.me();
                setScopes(
                    me.scopes
                        .map((s) => ({name: s.name, url: scopeUrlPart(s)}))
                        .filter((s) => s.url !== dstScope),
                );
            } catch (e) {
                setErr((e as Error).message || "failed to load scopes");
            }
        })();
    }, [dstScope]);

    useEffect(() => {
        setSelected(new Set());
        setResult(null);
        if (!srcScope) {
            setFiles([]);
            return;
        }
        setLoadingFiles(true);
        void (async () => {
            try {
                setFiles(await viewerApi.listFiles(srcScope));
                setErr(null);
            } catch (e) {
                setErr((e as Error).message || "listing failed");
                setFiles([]);
            } finally {
                setLoadingFiles(false);
            }
        })();
    }, [srcScope]);

    const shown = useMemo(
        () => files.filter((f) => f.key.toLowerCase().includes(filter.toLowerCase())),
        [files, filter],
    );
    const toggle = (key: string) => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key); else next.add(key);
        return next;
    });
    // Batch select/deselect — folder checkbox in tree mode passes every
    // descendant key at once (recursive select).
    const setSelection = useCallback((keys: string[], select: boolean) => {
        setSelected((prev) => {
            const next = new Set(prev);
            if (select) keys.forEach((k) => next.add(k));
            else keys.forEach((k) => next.delete(k));
            return next;
        });
    }, []);
    const tree = useMemo(() => buildFileTree(shown, (f) => f.key), [shown]);
    const allShownSelected = shown.length > 0 && shown.every((f) => selected.has(f.key));
    const toggleAll = () => setSelected((prev) => {
        const next = new Set(prev);
        if (allShownSelected) shown.forEach((f) => next.delete(f.key));
        else shown.forEach((f) => next.add(f.key));
        return next;
    });

    const onCopy = useCallback(async () => {
        if (selected.size === 0) return;
        setBusy(true);
        setErr(null);
        try {
            const r = await viewerApi.adminCopyKeysFromScope(dstScope, srcScope, Array.from(selected));
            setResult({copied: r.copied.length, skipped: r.skipped.length, failed: r.failed});
            // Copied keys are now "in corpus" — fold them in so a follow-up
            // copy greys them out too without a modal reopen.
            if (r.copied.length > 0) {
                setExistingKeys((prev) => {
                    const next = new Set(prev);
                    r.copied.forEach((c) => next.add(c.key));
                    return next;
                });
            }
            onCopied();
            if (r.failed.length === 0) setSelected(new Set());
        } catch (e) {
            setErr((e as Error).message || "copy failed");
        } finally {
            setBusy(false);
        }
    }, [dstScope, srcScope, selected, onCopied]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
            <div
                className="bg-gray-900 border border-gray-700 rounded-md w-full max-w-2xl max-h-[85vh] flex flex-col"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-100">
                        Copy files into <span className="font-mono">{dstSlug}</span>
                    </h3>
                    <button type="button" onClick={onClose} className="text-gray-400 hover:text-white text-lg leading-none px-1">×</button>
                </div>
                <div className="px-4 py-3 border-b border-gray-700 flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-gray-300">From scope</span>
                    <select
                        value={srcScope}
                        onChange={(e) => setSrcScope(e.target.value)}
                        className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100"
                    >
                        <option value="">Select a scope…</option>
                        {scopes.map((s) => (
                            <option key={s.url} value={s.url}>{s.name} ({s.url})</option>
                        ))}
                    </select>
                    {files.length > 0 && (
                        <input
                            type="text"
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            placeholder="filter…"
                            className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-sm text-gray-100 flex-1 min-w-[120px]"
                        />
                    )}
                    {files.length > 0 && (
                        <ViewModeToggle mode={viewMode} onChange={setViewMode}/>
                    )}
                </div>
                <div className="flex-1 min-h-0 overflow-auto">
                    {loadingFiles && <div className="text-xs text-gray-500 px-4 py-4">Loading…</div>}
                    {!loadingFiles && srcScope && shown.length === 0 && (
                        <div className="text-xs text-gray-500 italic px-4 py-4">
                            No files{filter ? " match the filter" : " in this scope"}.
                        </div>
                    )}
                    {shown.length > 0 && viewMode === "flat" && (
                        <DataTable
                            wrap={false}
                            columns={copyColumns(allShownSelected, toggleAll, selected, toggle)}
                            rows={shown}
                            rowKey={(f) => f.key}
                            className="w-full text-xs"
                            stickyHeader
                            theadClassName="bg-gray-900"
                            rowClassName="hover:bg-gray-800/40 cursor-pointer"
                            rowProps={(f) => ({onClick: () => toggle(f.key)})}
                        />
                    )}
                    {shown.length > 0 && viewMode === "tree" && (
                        <div className="px-1 py-1">
                            <FileTreeView
                                nodes={tree}
                                getKey={(f) => f.key}
                                namespace="corpus-copy"
                                scope={srcScope}
                                selection={{selected, onSelect: setSelection}}
                                isDisabled={(f) => existingKeys.has(f.key)}
                                renderFileTail={(f) => (
                                    existingKeys.has(f.key) ? (
                                        <span className="text-[10px] text-gray-500 uppercase tracking-wide">in corpus</span>
                                    ) : (
                                        <span className="text-gray-400 font-mono">{formatBytes(f.size)}</span>
                                    )
                                )}
                            />
                        </div>
                    )}
                </div>
                {err && <div className="text-xs text-red-400 px-4 py-2">{err}</div>}
                {result && (
                    <div className="text-xs px-4 py-2 border-t border-gray-700">
                        <span className="text-emerald-400">copied {result.copied}</span>
                        {result.skipped > 0 && (
                            <span className="text-gray-400">
                                {" "}· skipped {result.skipped} (already in corpus)
                            </span>
                        )}
                        {result.failed.length > 0 && (
                            <span className="text-amber-400" title={result.failed.map((f) => `${f.key}: ${f.reason}`).join("\n")}>
                                {" "}· failed {result.failed.length}
                            </span>
                        )}
                    </div>
                )}
                <div className="px-4 py-3 border-t border-gray-700 flex justify-end gap-2">
                    <button type="button" onClick={onClose} className="text-sm px-3 py-1 rounded-sm text-gray-300 hover:bg-gray-800">Close</button>
                    <button
                        type="button"
                        onClick={() => void onCopy()}
                        disabled={busy || selected.size === 0}
                        className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                    >
                        {busy ? "Copying…" : `Copy ${selected.size || ""} file${selected.size === 1 ? "" : "s"}`.trim()}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default CopyFromScopeModal;
