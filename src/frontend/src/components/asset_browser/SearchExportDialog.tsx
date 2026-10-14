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
// GEOMETRY. "Design geometry" writes IFCs from the design system's own geometry -- exact, but
// whatever it has not published yet must be requested from it first (the preflight's prompt), which
// is the slow path. "Published mesh" writes the hits' triangles as GLBs, cut from what the hits' own
// provider already published: a quick look. Its preflight asks one thing -- has each hit's provider
// published the project tree the mesh is cut from? (a search index is filled by a different job) --
// and offers that provider's collection request for the ones it has not, or the export of what is
// ready. A mesh file asks for its design geometry later, one node at a time, from its provenance
// (GalleryProvenance).
//
// MERGE. "One file per collection" writes each collection's hits into one file under their shared
// levels instead of a file per hit -- far fewer files, and a higher cap, since the cap's argument
// was a folder nobody browses. The collections the panel left out (by clicking their counts) are
// already out of `query`, `picked` and `total`; the dialog only says which they are.
//
// AFTER. The run is watched in the global toast at once (`trackExport`) and opened in the panel's
// Export runs list; the dialog closes. Nothing here waits for the run.

import React, {useEffect, useMemo, useState} from "react";
import {createPortal} from "react-dom";

import {
    defaultExportFolder,
    exportQuery,
    exportTargetScopes,
    folderSuggestions,
    geometryPrompt,
    mapLimited,
    mergeMeshPreflights,
    mergePreflights,
    meshPrompt,
    normalizeFolder,
    queryForCollection,
    type GeometryPrompt,
    type MeshPrompt,
} from "@/assets/searchExport";
import type {SearchHit, SearchQuery} from "@/services/api/attributeSearch";
import {searchExportApi, type ExportAlso, type ExportGeometry, type ExportHit, type ExportRun} from "@/services/api/searchExport";
import {viewerApi, ApiError} from "@/services/viewerApi";
import {trackExport, trackTreeRequest} from "@/services/sweepTracking";
import {useScopeStore} from "@/state/scopeStore";

const CONTROL = "h-7 rounded-md border border-gray-700 bg-gray-800 px-2 text-xs text-gray-100 placeholder:text-gray-500";
const LABEL = "text-[11px] uppercase tracking-wide text-gray-400";
/** The server's cap on one run; mirrored only to warn before the round trip. */
export const EXPORT_HIT_CAP = 2000;
/** The cap on a merged run (one file per collection), likewise the server's. */
export const MERGED_EXPORT_HIT_CAP = 10000;
/** Collections the preflight checks at once when it goes a collection at a time. */
const PREFLIGHT_WIDTH = 4;

function message(e: unknown): string {
    return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
}

/** Whether a preflight failure means "this API has no preflight" (an older server: export as before)
 *  rather than "the preflight said no" -- which is shown, not stepped over. Stepping over every
 *  failure is how a 409 from the preflight once turned into an export of thousands of hits with no tree. */
export function preflightUnavailable(e: unknown): boolean {
    return e instanceof ApiError && (e.status === 404 || e.status === 405);
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
    /** Every match of the query (the sum of its counts, less the collections left out). */
    total: number;
    /** Collections the panel left out (already excluded in `query` and `picked`); shown, not applied. */
    leftOut?: string[];
    /** The collections with matches being exported: the preflight checks them a few at a time, so
     *  its progress can be shown. Absent or one: one check of the whole query. */
    collections?: string[];
    onClose: () => void;
    onStarted: (run: ExportRun) => void;
}> = ({scope, query, picked, total, leftOut = [], collections = [], onClose, onStarted}) => {
    const available = useScopeStore((s) => s.available);
    const targets = useMemo(() => exportTargetScopes(available), [available]);
    const [target, setTarget] = useState(() => (targets.some((t) => t.url === scope) ? scope : (targets[0]?.url ?? "user:me")));
    const [folder, setFolder] = useState(defaultExportFolder);
    const [which, setWhich] = useState<"selected" | "all">(picked.length ? "selected" : "all");
    const [geometry, setGeometry] = useState<ExportGeometry>("design");
    const mesh = geometry === "mesh";
    const [withAttrs, setWithAttrs] = useState(true);
    /** One file per collection rather than one per hit. */
    const [merge, setMerge] = useState(false);
    const [also, setAlso] = useState<ExportAlso[]>([]);
    const [busy, setBusy] = useState(false);
    const [checking, setChecking] = useState(false);
    /** The preflight's progress when it runs a collection at a time; null otherwise. */
    const [checkProgress, setCheckProgress] = useState<{done: number; total: number} | null>(null);
    const [error, setError] = useState<string | null>(null);
    /** The missing-geometry question, once the preflight found something to ask about. */
    const [prompt, setPrompt] = useState<GeometryPrompt | null>(null);
    /** The mesh export's question: collections whose provider published no project tree yet. */
    const [meshAsk, setMeshAsk] = useState<MeshPrompt | null>(null);
    /** Tree requests started from this dialog, by provider: the run, or why it did not start. */
    const [treeRequests, setTreeRequests] = useState<Record<string, {ok: boolean; text: string}>>({});
    // The answer was about THESE hits and this geometry: another choice asks again.
    useEffect(() => {
        setPrompt(null);
        setMeshAsk(null);
    }, [which, geometry]);

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
    const cap = merge ? MERGED_EXPORT_HIT_CAP : EXPORT_HIT_CAP;
    const overCap = count > cap;
    const toggleAlso = (f: ExportAlso, on: boolean) => setAlso((cur) => (on ? [...cur.filter((x) => x !== f), f] : cur.filter((x) => x !== f)));

    /** The preflight, a collection at a time (`PREFLIGHT_WIDTH` at once) when there are several, so
     *  the wait shows its progress; one request otherwise. `one` asks for one part, `merge` adds the
     *  parts up into the answer one request would have given. */
    async function checkInParts<P>(
        one: (body: {query: SearchQuery; hits: ExportHit[] | null}) => Promise<P>,
        merge: (parts: P[]) => P,
    ): Promise<P> {
        let units: {query: SearchQuery; hits: ExportHit[] | null}[];
        if (which === "selected") {
            const by = new Map<string, ExportHit[]>();
            for (const h of picked) by.set(h.collection, [...(by.get(h.collection) ?? []), exportHit(h)]);
            units = [...by].map(([c, hits]) => ({query: queryForCollection(query, c), hits}));
        } else {
            units = collections.map((c) => ({query: queryForCollection(query, c), hits: null}));
        }
        if (units.length <= 1) return one({query, hits: which === "selected" ? picked.map(exportHit) : null});
        setCheckProgress({done: 0, total: units.length});
        const parts = await mapLimited(units, PREFLIGHT_WIDTH, one, (done) => setCheckProgress({done, total: units.length}));
        return merge(parts);
    }

    /** Start the export. `leaveOut`: collections to leave out of it as well -- the mesh prompt's
     *  "export what is ready" leaves out those with nothing to write. */
    const start = async (requestMissing: boolean, leaveOut: readonly string[] = [], requestTrees = false) => {
        const f = normalizeFolder(folder);
        if (!f.ok) {
            setError(f.error);
            return;
        }
        setBusy(true);
        setError(null);
        const out = new Set(leaveOut);
        try {
            const run = await searchExportApi.startSearchExport(scope, {
                query: out.size ? exportQuery(query, out) : query,
                hits: which === "selected" ? picked.filter((h) => !out.has(h.collection)).map(exportHit) : null,
                target_scope: target,
                target_folder: f.folder,
                include_source_attributes: withAttrs,
                merge,
                // A mesh export is the GLB: nothing derived, nothing requested.
                ...(mesh
                    ? {geometry, also: [], request_missing_geometry: false, request_trees: requestTrees}
                    : {also, request_missing_geometry: requestMissing}),
            });
            const kind = mesh ? (requestTrees ? "Request trees + mesh export" : "Mesh export") : requestMissing ? "Request geometry + export" : "Export";
            const what = `${kind}${merge ? " (merged)" : ""}`;
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
        // A mesh export asks whether each hit's own provider published the tree its mesh is cut from:
        // a search index is filled by a different job, so a fresh one finds hits with no tree at all.
        if (mesh) {
            setBusy(true);
            setError(null);
            setChecking(true);
            let ask: MeshPrompt | null = null;
            try {
                ask = meshPrompt(await checkInParts((b) => searchExportApi.meshExportPreflight(scope, b), mergeMeshPreflights));
            } catch (e) {
                if (!preflightUnavailable(e)) {
                    setError(`Could not check the hits before exporting: ${message(e)}`);
                    return;
                }
                console.warn("mesh export preflight unavailable; exporting without it", e);
            } finally {
                setChecking(false);
                setCheckProgress(null);
                setBusy(false);
            }
            if (ask?.ask) setMeshAsk(ask);
            else await start(false);
            return;
        }
        setBusy(true);
        setError(null);
        setChecking(true);
        let pre: GeometryPrompt | null = null;
        try {
            pre = geometryPrompt(await checkInParts((b) => searchExportApi.exportPreflight(scope, b), mergePreflights));
        } catch (e) {
            if (!preflightUnavailable(e)) {
                setError(`Could not check the hits before exporting: ${message(e)}`);
                return;
            }
            console.warn("export preflight unavailable; exporting without it", e);
        } finally {
            setChecking(false);
            setCheckProgress(null);
            setBusy(false);
        }
        if (pre?.ask) setPrompt(pre);
        else await start(false);
    };

    /** Ask a provider for the trees it has not published, watched in the toast. The dialog stays:
     *  what is ready can still be exported now, and the rest after the request finishes. */
    const requestTrees = async (r: MeshPrompt["requests"][number]) => {
        setBusy(true);
        try {
            const run = await searchExportApi.requestTrees(scope, r.provider, r.collections);
            trackTreeRequest(scope, run.id, `${r.label}: ${r.collections.length} collection${r.collections.length === 1 ? "" : "s"}`);
            setTreeRequests((cur) => ({
                ...cur,
                [r.provider]: {ok: true, text: "Requested -- follow it in the toast, then export again."},
            }));
        } catch (e) {
            setTreeRequests((cur) => ({...cur, [r.provider]: {ok: false, text: message(e)}}));
        } finally {
            setBusy(false);
        }
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
                        {merge
                            ? `One ${mesh ? "GLB" : "IFC"} per collection holding all of its hits, written into the folder with its provenance beside it.`
                            : `One ${mesh ? "GLB" : "IFC"} per hit, written under the folder by collection, each with its provenance beside it.`}
                    </div>

                    <div className="space-y-1">
                        <div className={LABEL}>Geometry</div>
                        <label className="flex items-start gap-2" title="The design system's own geometry, as IFC">
                            <input type="radio" name="export-geometry" className="mt-0.5" checked={!mesh} onChange={() => setGeometry("design")} />
                            <span>
                                Design geometry (IFC)
                                <span className="block text-gray-500">
                                    Exact. Anything the design system has not exported yet is requested from it first -- slow.
                                </span>
                            </span>
                        </label>
                        <label className="flex items-start gap-2" title="The hits' triangles as their own provider published them, as GLB">
                            <input type="radio" name="export-geometry" className="mt-0.5" checked={mesh} onChange={() => setGeometry("mesh")} />
                            <span>
                                Published mesh (GLB)
                                <span className="block text-gray-500">
                                    Quick look from what the results' provider already published. Request a file's design geometry from its
                                    provenance later.
                                </span>
                            </span>
                        </label>
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
                        {leftOut.length > 0 && (
                            <div className="text-gray-400 break-words" title="Click a collection's count in the results to put it back">
                                Leaving out {leftOut.length} collection{leftOut.length === 1 ? "" : "s"}: {leftOut.join(", ")}
                            </div>
                        )}
                        <label
                            className="flex items-start gap-2 pt-1"
                            title="Fewer files: each collection's hits together in one file, under their shared levels"
                        >
                            <input type="checkbox" className="mt-0.5" checked={merge} onChange={(e) => setMerge(e.target.checked)} />
                            <span>
                                One file per collection
                                <span className="block text-gray-500">
                                    Merge every hit of a collection into one {mesh ? "GLB" : "IFC"}. Up to {MERGED_EXPORT_HIT_CAP.toLocaleString()}{" "}
                                    hits; a merged file cannot request design geometry for one node later.
                                </span>
                            </span>
                        </label>
                        {overCap && (
                            <div className="text-amber-300">
                                {count.toLocaleString()} hits is over the {cap.toLocaleString()} one {merge ? "merged " : ""}export takes --{" "}
                                {merge ? "narrow the search, or leave collections out." : "merge per collection, narrow the search, or pick hits."}
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

                    {!mesh && (
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
                    )}

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

                    {meshAsk && (
                        <div role="alertdialog" aria-label="Missing project trees" className="rounded-md border border-amber-700/70 bg-amber-950/40 p-2 space-y-2">
                            <div className="text-amber-200 font-medium">{meshAsk.headline}</div>
                            <div className="text-gray-300">
                                A mesh is cut from the project tree its provider published, which the search index does not
                                need -- so a collection can be searchable and still have nothing to cut from.
                            </div>
                            {meshAsk.requests.map((r) => {
                                const done = treeRequests[r.provider];
                                return (
                                    <div key={r.provider} className="space-y-1">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <button
                                                type="button"
                                                className="h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                                                disabled={busy || r.cannot != null || done?.ok}
                                                title={r.collections.join(", ")}
                                                onClick={() => void requestTrees(r)}
                                            >
                                                Only request: {r.label} ({r.collections.length} collection{r.collections.length === 1 ? "" : "s"})
                                            </button>
                                            {done && <span className={done.ok ? "text-emerald-300" : "text-red-300"}>{done.text}</span>}
                                        </div>
                                        {r.cannot && <div className="text-red-300">Cannot request: {r.cannot}</div>}
                                        {r.sweepNote && <div className="text-amber-300">{r.sweepNote}</div>}
                                    </div>
                                );
                            })}
                            {meshAsk.unknown.length > 0 && (
                                <div className="space-y-1">
                                    <div className="text-gray-300">Not in their provider's tree (a tree request will not add these):</div>
                                    {meshAsk.unknown.map((u) => (
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

                    {checking && (
                        <div className="space-y-1" role="status" aria-live="polite">
                            <div className="text-gray-400">
                                {checkProgress
                                    ? `Checking which hits have geometry: ${checkProgress.done} of ${checkProgress.total} collections`
                                    : "Checking which hits have geometry…"}
                            </div>
                            {checkProgress && (
                                <div className="h-1.5 overflow-hidden rounded-sm bg-gray-800">
                                    <div
                                        className="h-full bg-blue-400 transition-[width] duration-300"
                                        style={{width: `${Math.round((100 * checkProgress.done) / Math.max(1, checkProgress.total))}%`}}
                                    />
                                </div>
                            )}
                        </div>
                    )}

                    {error && <div className="rounded-sm bg-red-900/60 px-2 py-1 text-red-100 break-words">{error}</div>}
                </div>
                {meshAsk ? (
                    <div className="px-4 py-2 border-t border-gray-700 flex flex-wrap justify-end gap-2">
                        {/* A request started from here runs on in the background (the toast), so
                            once one has, leaving the dialog is "Close", not "Back". */}
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                            disabled={busy}
                            onClick={() => (Object.values(treeRequests).some((t) => t.ok) ? onClose() : setMeshAsk(null))}
                        >
                            {Object.values(treeRequests).some((t) => t.ok) ? "Close" : "Back"}
                        </button>
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                            disabled={busy || meshAsk.ready === 0}
                            title={
                                meshAsk.emptyCollections.length
                                    ? `Leaves out ${meshAsk.emptyCollections.length} collection(s) with nothing to cut: ${meshAsk.emptyCollections.join(", ")}`
                                    : undefined
                            }
                            onClick={() => void start(false, meshAsk.emptyCollections)}
                        >
                            {`Export only the ${meshAsk.ready.toLocaleString()} that have one`}
                        </button>
                        <button
                            type="button"
                            className="h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50"
                            disabled={busy || !meshAsk.requests.length || meshAsk.requests.some((r) => r.cannot != null)}
                            title="One run, in the background: each collection with no tree asks its provider for it, then its meshes are built and written. Follow it in the toast."
                            onClick={() => void start(false, [], true)}
                        >
                            {busy ? "Starting…" : "Request trees, then export all"}
                        </button>
                    </div>
                ) : prompt ? (
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
                        {checking
                            ? `Checking geometry…${checkProgress ? ` ${checkProgress.done}/${checkProgress.total}` : ""}`
                            : busy
                              ? "Starting…"
                              : `Export ${count.toLocaleString()}`}
                    </button>
                </div>
                )}
            </div>
        </div>,
        document.body,
    );
};

export default SearchExportDialog;
