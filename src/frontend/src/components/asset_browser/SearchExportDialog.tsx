// "Export results…": write one model per hit into a folder, from the attribute search panel.
//
// WHAT IS EXPORTED is the query the results answer (`lastQuery`) plus either the picked hits or
// none -- "All matches" sends `hits: null` and the server pages through the query itself, so an
// export of every match never waits on the panel having loaded every page. The server caps a run
// (2000 hits); over it, the start answers 409 with a sentence saying so, shown as is.
//
// WHERE TO. Any scope the user can see and might write -- their own, shared, their projects
// (`exportTargetScopes`); the server has the last word (403). The folder is typed, with the
// target's existing folders offered (`folderSuggestions` over its listing), so adding to an earlier
// export's folder is one pick.
//
// AFTER. The run is watched in the global toast at once (`trackExport`) and opened in the panel's
// Export runs list; the dialog closes. Nothing here waits for the run.

import React, {useEffect, useMemo, useState} from "react";
import {createPortal} from "react-dom";

import {
    defaultExportFolder,
    exportTargetScopes,
    folderSuggestions,
    geometryPrompt,
    normalizeFolder,
    type GeometryPrompt,
} from "@/assets/searchExport";
import type {SearchHit, SearchQuery} from "@/services/api/attributeSearch";
import {searchExportApi, type ExportAlso, type ExportHit, type ExportRun} from "@/services/api/searchExport";
import {viewerApi, ApiError} from "@/services/viewerApi";
import {trackExport} from "@/services/sweepTracking";
import {useScopeStore} from "@/state/scopeStore";

const CONTROL = "h-7 rounded-md border border-gray-700 bg-gray-800 px-2 text-xs text-gray-100 placeholder:text-gray-500";
const LABEL = "text-[11px] uppercase tracking-wide text-gray-400";
/** The server's cap on one run; mirrored only to warn before the round trip. */
export const EXPORT_HIT_CAP = 2000;

function message(e: unknown): string {
    return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

/** A hit as the export takes it. */
function exportHit(h: SearchHit): ExportHit {
    return {
        provider: h.provider,
        collection: h.collection,
        node_id: h.node_id,
        full_name: h.full_name,
        source_ref: h.source_ref,
        kind: h.kind,
        depth: h.depth,
        path: h.path ?? [],
        attrs: h.attrs ?? {},
    };
}

const SearchExportDialog: React.FC<{
    /** The searched scope -- where the run lives. */
    scope: string;
    query: SearchQuery;
    picked: SearchHit[];
    /** Every match of the query (the sum of its counts). */
    total: number;
    onClose: () => void;
    onStarted: (run: ExportRun) => void;
}> = ({scope, query, picked, total, onClose, onStarted}) => {
    const available = useScopeStore((s) => s.available);
    const targets = useMemo(() => exportTargetScopes(available), [available]);
    const [target, setTarget] = useState(() => (targets.some((t) => t.url === scope) ? scope : (targets[0]?.url ?? "user:me")));
    const [folder, setFolder] = useState(defaultExportFolder);
    const [which, setWhich] = useState<"selected" | "all">(picked.length ? "selected" : "all");
    const [withAttrs, setWithAttrs] = useState(true);
    const [also, setAlso] = useState<ExportAlso[]>([]);
    const [busy, setBusy] = useState(false);
    const [checking, setChecking] = useState(false);
    const [error, setError] = useState<string | null>(null);
    /** The missing-geometry question, once the preflight found something to ask about. */
    const [prompt, setPrompt] = useState<GeometryPrompt | null>(null);
    // The answer was about THESE hits: another choice of hits asks again.
    useEffect(() => setPrompt(null), [which]);

    // The target's folders, for the suggestions. A listing that fails only loses the suggestions.
    const [keys, setKeys] = useState<string[]>([]);
    useEffect(() => {
        let live = true;
        setKeys([]);
        viewerApi
            .listFiles(target)
            .then((files) => live && setKeys(files.map((f) => f.key)))
            .catch(() => {});
        return () => {
            live = false;
        };
    }, [target]);
    const suggestions = useMemo(() => folderSuggestions(keys, folder), [keys, folder]);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [busy, onClose]);

    const count = which === "selected" ? picked.length : total;
    const overCap = count > EXPORT_HIT_CAP;
    const toggleAlso = (f: ExportAlso, on: boolean) => setAlso((cur) => (on ? [...cur.filter((x) => x !== f), f] : cur.filter((x) => x !== f)));

    const start = async (requestMissing: boolean) => {
        const f = normalizeFolder(folder);
        if (!f.ok) {
            setError(f.error);
            return;
        }
        setBusy(true);
        setError(null);
        try {
            const run = await searchExportApi.startSearchExport(scope, {
                query,
                hits: which === "selected" ? picked.map(exportHit) : null,
                target_scope: target,
                target_folder: f.folder,
                include_source_attributes: withAttrs,
                also,
                request_missing_geometry: requestMissing,
            });
            const what = requestMissing ? "Request geometry + export" : "Export";
            trackExport(scope, run.id, `${what} ${run.hits || count} → ${target}/${f.folder}`);
            onStarted(run);
            onClose();
        } catch (e) {
            setError(message(e));
        } finally {
            setBusy(false);
        }
    };

    // Export: first ask what lacks geometry (writes nothing). All there -> start at once; anything
    // missing -> the prompt decides. A preflight that fails (an older API without it, say) does not
    // block the export: it starts as it always did, without requesting anything.
    const submit = async () => {
        const f = normalizeFolder(folder);
        if (!f.ok) {
            setError(f.error);
            return;
        }
        setBusy(true);
        setError(null);
        setChecking(true);
        let pre: GeometryPrompt | null = null;
        try {
            pre = geometryPrompt(
                await searchExportApi.exportPreflight(scope, {query, hits: which === "selected" ? picked.map(exportHit) : null}),
            );
        } catch (e) {
            console.warn("export preflight failed; exporting without it", e);
        } finally {
            setChecking(false);
            setBusy(false);
        }
        if (pre?.ask) setPrompt(pre);
        else await start(false);
    };

    return createPortal(
        <div
            className="fixed inset-0 z-[70] flex items-start justify-center bg-black/40 p-4 sm:p-8 overflow-y-auto"
            onMouseDown={(e) => {
                if (e.target === e.currentTarget && !busy) onClose();
            }}
        >
            <div
                role="dialog"
                aria-label="Export results"
                className="bg-gray-900 text-gray-100 border border-gray-700 rounded-md shadow-xl w-[30rem] max-w-[92vw] flex flex-col text-xs"
                onMouseDown={(e) => e.stopPropagation()}
            >
                <div className="px-4 py-2 border-b border-gray-700 font-medium">Export results</div>
                <div className="p-4 space-y-3">
                    <div className="text-gray-400">
                        One IFC per hit, written under the folder by collection, each with its provenance beside it.
                    </div>

                    <div className="space-y-1">
                        <div className={LABEL}>Export</div>
                        <label className={`flex items-center gap-2 ${picked.length ? "" : "opacity-50"}`}>
                            <input type="radio" name="export-which" disabled={!picked.length} checked={which === "selected"} onChange={() => setWhich("selected")} />
                            Selected ({picked.length.toLocaleString()})
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="radio" name="export-which" checked={which === "all"} onChange={() => setWhich("all")} />
                            All matches ({total.toLocaleString()})
                        </label>
                        {overCap && (
                            <div className="text-amber-300">
                                {count.toLocaleString()} hits is over the {EXPORT_HIT_CAP.toLocaleString()} one export takes -- narrow the
                                search, or pick hits.
                            </div>
                        )}
                    </div>

                    <div className="space-y-1">
                        <div className={LABEL}>Into</div>
                        <select aria-label="Target scope" className={`${CONTROL} w-full`} value={target} onChange={(e) => setTarget(e.target.value)}>
                            {targets.map((t) => (
                                <option key={t.url} value={t.url}>
                                    {t.label}
                                </option>
                            ))}
                        </select>
                        <input
                            aria-label="Folder"
                            className={`${CONTROL} w-full font-mono`}
                            list="search-export-folders"
                            placeholder="exports/my-export"
                            value={folder}
                            onChange={(e) => setFolder(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && !busy && !overCap && void submit()}
                        />
                        <datalist id="search-export-folders">
                            {suggestions.map((s) => (
                                <option key={s} value={s} />
                            ))}
                        </datalist>
                    </div>

                    <div className="space-y-1">
                        <div className={LABEL}>Options</div>
                        <label className="flex items-center gap-2" title="Write each element's source attributes into the IFC as property sets">
                            <input type="checkbox" checked={withAttrs} onChange={(e) => setWithAttrs(e.target.checked)} />
                            Include source attributes
                        </label>
                        <div className="flex items-center gap-4">
                            <label className="flex items-center gap-2">
                                <input type="checkbox" checked={also.includes("glb")} onChange={(e) => toggleAlso("glb", e.target.checked)} />
                                Also GLB
                            </label>
                            <label className="flex items-center gap-2">
                                <input type="checkbox" checked={also.includes("step")} onChange={(e) => toggleAlso("step", e.target.checked)} />
                                Also STEP
                            </label>
                        </div>
                    </div>

                    {prompt && (
                        <div role="alertdialog" aria-label="Missing geometry" className="rounded-md border border-amber-700/70 bg-amber-950/40 p-2 space-y-2">
                            <div className="text-amber-200 font-medium">{prompt.headline}</div>
                            {prompt.requestable > 0 && (
                                <div className="text-gray-300">
                                    Queue a geometry export for {prompt.requestable.toLocaleString()} of them on the design-system worker first?{" "}
                                    {prompt.jobs.toLocaleString()} request job{prompt.jobs === 1 ? "" : "s"}, one project session each; slow (minutes per
                                    project), and it shares the worker's seat with anything else running -- the jobs queue behind it.
                                </div>
                            )}
                            {prompt.cannotRequest && <div className="text-red-300">Cannot request: {prompt.cannotRequest}</div>}
                            {prompt.sweepNote && <div className="text-amber-300">{prompt.sweepNote}</div>}
                            {prompt.unknown.length > 0 && (
                                <div className="space-y-1">
                                    <div className="text-gray-300">Not in the design system's published tree (a request by node will not find these):</div>
                                    {prompt.unknown.map((u) => (
                                        <div key={u.collection} className="pl-2 text-gray-400 break-words">
                                            <span className="text-gray-200">
                                                {u.collection} {u.count.toLocaleString()}
                                            </span>
                                            {u.examples.length > 0 && <span className="font-mono"> ({u.examples.join(", ")}{u.count > u.examples.length ? ", …" : ""})</span>}
                                            {u.reason && <div className="text-gray-500">{u.reason}</div>}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {error && <div className="rounded-sm bg-red-900/60 px-2 py-1 text-red-100 break-words">{error}</div>}
                </div>
                {prompt ? (
                    <div className="px-4 py-2 border-t border-gray-700 flex flex-wrap justify-end gap-2">
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                            disabled={busy}
                            onClick={() => setPrompt(null)}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                            disabled={busy}
                            onClick={() => void start(false)}
                        >
                            Export only what has geometry
                        </button>
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50"
                            disabled={busy || prompt.requestable === 0 || prompt.cannotRequest != null}
                            onClick={() => void start(true)}
                        >
                            {busy ? "Starting…" : "Queue geometry export, then export"}
                        </button>
                    </div>
                ) : (
                <div className="px-4 py-2 border-t border-gray-700 flex justify-end gap-2">
                    <button
                        type="button"
                        className="h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                        disabled={busy}
                        onClick={onClose}
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        className="h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50"
                        disabled={busy || count === 0 || overCap || !targets.length}
                        onClick={() => void submit()}
                    >
                        {checking ? "Checking geometry…" : busy ? "Starting…" : `Export ${count.toLocaleString()}`}
                    </button>
                </div>
                )}
            </div>
        </div>,
        document.body,
    );
};

export default SearchExportDialog;
