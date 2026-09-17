import React, {useCallback, useEffect, useMemo, useState} from "react";

import {fuzzyFilter} from "@/services/fuzzy";
import {makePluginContextStandalone} from "@/plugins";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {useExternalModelsStore} from "@/state/externalModelsStore";
import {
    ExternalModel,
    bindingFor,
    catalogueNonce,
    isHidden,
    listModelsDetailed,
    loadBindingMap,
    modelUrl,
    uploadModel,
} from "@/services/externalModels";

// The menu-bar list of externally-stored models for the current scope.
//
// Shows only when the scope is BOUND (Admin -> External Models). An unbound
// deployment gets no panel and no button, which is why the binding tab is the
// entry point for the whole feature rather than a detail of it.
//
// Loading goes through the same scene primitives a plugin would use, via the
// standalone plugin context, so an external model is registered, disposed and
// listed exactly like any other loaded source — one implementation of that
// path, not two.

const OWNER = "external-models";

const ExternalModelsPanel: React.FC = () => {
    const visible = useExternalModelsStore((s) => s.visible);
    const scope = useScopeStore((s) => scopeUrlPart(s.current));

    const [models, setModels] = useState<ExternalModel[]>([]);
    const [binding, setBinding] = useState<{provider: string; collection: string; hide: string[]} | null>(
        null,
    );
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [loaded, setLoaded] = useState<Set<string>>(new Set());
    const [busy, setBusy] = useState<string | null>(null);
    // Whether THIS provider accepts models. Declared by the provider, not
    // configured here, so a read-only catalogue never shows the control at all
    // rather than showing one that fails when pressed.
    const [uploadable, setUploadable] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [query, setQuery] = useState("");
    // `null` when no bulk load is running. While one is, it is the progress a
    // person needs to decide whether to wait: which model, and how far in.
    const [bulk, setBulk] = useState<{done: number; total: number; name: string} | null>(null);
    const cancelBulk = React.useRef(false);
    const fileRef = React.useRef<HTMLInputElement | null>(null);

    // ORDER IS DECIDED HERE, not taken from the provider. The built-in S3
    // catalogue happens to sort its listing, but that is its choice and not a
    // promise of the interface -- a browser-side provider returns whatever its
    // vendor API returned, and a list that is alphabetical for one catalogue
    // and arbitrary for the next is worse than one that is always arbitrary,
    // because you stop trusting the order you can see.
    //
    // A collection may hold one entry per SITE across every source export, which is hundreds of near-identical names. Scrolling that is not
    // a way to find anything, so the filter is not a refinement of the list --
    // it is how the list is used.
    // FILTERED ON BOTH. Moving the model file out of the name would otherwise
    // make it unsearchable, and the name of a source export is a
    // perfectly reasonable thing to type when looking for one.
    // THE SCOPE'S OWN FILTER, applied before anything the user typed. An admin
    // binds a scope and says which models are irrelevant to it -- the temporary
    // steel exports, the volume models -- and this is where that takes effect.
    // It matters most for "Load all", which would otherwise pull in exactly the
    // models someone had already declared unwanted.
    //
    // Hidden here rather than at the provider: the provider reports what the
    // catalogue HOLDS, which is a fact, and which models a scope cares about is
    // a decision this deployment made about itself.
    const visibleModels = useMemo(
        () => (binding?.hide?.length ? models.filter((m) => !isHidden(m, binding.hide)) : models),
        [models, binding],
    );

    const shown = useMemo(
        () =>
            fuzzyFilter(visibleModels, query, (m) =>
                m.description ? `${m.name} ${m.description}` : m.name,
            ),
        [visibleModels, query],
    );

    useEffect(() => {
        if (!visible) return;
        let cancelled = false;
        const abort = new AbortController();
        void (async () => {
            setLoading(true);
            setError(null);
            setModels([]);
            try {
                const b = bindingFor(await loadBindingMap(), scope);
                if (cancelled) return;
                setBinding(b);
                if (!b) return;
                // Fresh each time the panel opens or the scope changes: core
                // caches an identical request indefinitely, so without this the
                // list could never reflect a changed catalogue.
                const listing = await listModelsDetailed(b.provider, b.collection, scope, {
                    signal: abort.signal,
                    refresh: catalogueNonce(),
                });
                if (!cancelled) {
                    setModels(listing.models);
                    setUploadable(listing.canUpload);
                }
            } catch (e) {
                // The provider's own message is passed through: a refusal here
                // may be a correct answer (no access to that collection), and it
                // is better said in the words of whoever owns that access model.
                if (!cancelled) setError(e instanceof Error ? e.message : String(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
            abort.abort();
        };
    }, [visible, scope]);

    const onUpload = useCallback(
        async (file: File) => {
            if (!binding) return;
            setUploading(true);
            setError(null);
            try {
                // The FILE NAME is the model id. It is what the catalogue keys
                // on and what every consumer will reference, so inventing one
                // here would make the uploader the only place that knows the
                // real name.
                await uploadModel(binding.provider, binding.collection, file.name, file, scope);
                // Re-read rather than splice the new model in: the catalogue is
                // the authority on what it now holds, and a locally invented row
                // would be the one thing on screen nobody had confirmed.
                const listing = await listModelsDetailed(binding.provider, binding.collection, scope, {
                    refresh: catalogueNonce(),
                });
                setModels(listing.models);
                setUploadable(listing.canUpload);
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setUploading(false);
            }
        },
        [binding, scope],
    );

    const sourceNameFor = useCallback(
        (m: ExternalModel) => `${OWNER}:${m.collection}/${m.id}`,
        [],
    );

    /** Load one model. Resolves to whether it made it into the scene.
     *
     *  IT REPORTS RATHER THAN ONLY SHOWING. It used to swallow its own failure
     *  into `error` and return nothing, which is fine for a row's own button
     *  and useless to a bulk caller: "Load all" could not tell nine successes
     *  and one failure from ten successes, and the last model's message would
     *  be the only one left standing. */
    const onLoad = useCallback(
        async (m: ExternalModel): Promise<boolean> => {
            if (!binding) return false;
            setBusy(m.id);
            setError(null);
            try {
                const {url, headers} = await modelUrl(
                    binding.provider, binding.collection, m.id, scope,
                );
                const ctx = makePluginContextStandalone(OWNER);
                await ctx.scene.loadModelFromUrl(OWNER, url, {
                    sourceName: sourceNameFor(m),
                    // Empty for a presigned URL; populated for a provider whose
                    // fetch must be authenticated. Passing them through means the
                    // panel works for both without branching on provider.
                    headers: Object.keys(headers).length ? headers : undefined,
                    // Third-party glTF follows the spec's Y-up convention while
                    // this viewer's world is Z-up, and the convention is baked
                    // into the vertex data with no node transform to detect it,
                    // so the caller has to declare it.
                    sourceUpAxis: "y",
                });
                setLoaded((prev) => new Set(prev).add(m.id));
                return true;
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
                return false;
            } finally {
                setBusy(null);
            }
        },
        [binding, scope, sourceNameFor],
    );

    //: Above this many, ask first. One collection can be hundreds of models of tens of
    //: megabytes each; "Load all" with an empty filter would fetch the lot and
    //: take the browser with it. The number is low enough that a deliberate
    //: bulk load still confirms, which is the point -- the accident this
    //: prevents is pressing it without having filtered.
    const CONFIRM_ABOVE = 8;

    const onLoadAll = useCallback(
        async (targets: ExternalModel[]) => {
            if (!binding || bulk) return;
            // Already-loaded models are skipped rather than reloaded: the button
            // means "have all of these in the scene", and re-fetching what is
            // already there is the expensive way to do nothing.
            const todo = targets.filter((m) => !loaded.has(m.id));
            if (todo.length === 0) return;
            if (
                todo.length > CONFIRM_ABOVE &&
                !window.confirm(
                    `Load ${todo.length} models into the scene?\n\n` +
                        `They are fetched one at a time and each can be tens of megabytes. ` +
                        `Filter the list first to load fewer.`,
                )
            ) {
                return;
            }

            cancelBulk.current = false;
            setError(null);
            const failed: string[] = [];

            // ONE AT A TIME, deliberately. Firing 200 fetches at once would
            // saturate the network and the GPU upload path, and the failure mode
            // is a viewer that appears hung. Sequential also means the scene
            // fills progressively, which is what makes waiting bearable.
            for (let i = 0; i < todo.length; i++) {
                if (cancelBulk.current) break;
                const m = todo[i];
                setBulk({done: i, total: todo.length, name: m.name});
                // Collected rather than thrown: one unreachable model must not
                // abandon the other two hundred.
                if (!(await onLoad(m))) failed.push(m.name);
            }

            setBulk(null);
            if (failed.length) {
                setError(
                    `${failed.length} of ${todo.length} did not load: ${failed.slice(0, 3).join(", ")}` +
                        (failed.length > 3 ? `, and ${failed.length - 3} more` : ""),
                );
            }
        },
        [binding, bulk, loaded, onLoad],
    );

    const onUnload = useCallback(
        (m: ExternalModel) => {
            makePluginContextStandalone(OWNER).scene.unloadModel(sourceNameFor(m));
            setLoaded((prev) => {
                const next = new Set(prev);
                next.delete(m.id);
                return next;
            });
        },
        [sourceNameFor],
    );

    if (!visible) return null;

    // text-gray-100 is not decoration: the panel sets a dark background but
    // inherited nothing for the foreground, so model names rendered dark on dark
    // and were effectively invisible.
    return (
        <div className="absolute top-12 right-2 z-20 w-80 max-h-[70vh] overflow-auto rounded-sm border border-gray-700 bg-gray-900/95 text-gray-100 shadow-lg">
            <div className="px-3 py-2 border-b border-gray-700">
                <div className="text-sm font-medium">External models</div>
                <div className="flex items-center gap-2">
                    <div className="text-xs text-gray-400 truncate flex-1">
                        {binding ? `${binding.provider} / ${binding.collection}` : "not bound for this scope"}
                    </div>
                    {binding && uploadable && (
                        <>
                            <input
                                ref={fileRef}
                                type="file"
                                accept=".glb,.gltf"
                                className="hidden"
                                onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    // Cleared so choosing the SAME file twice
                                    // fires again — a re-upload after a failed
                                    // one is the obvious next action.
                                    e.target.value = "";
                                    if (f) void onUpload(f);
                                }}
                            />
                            <button
                                type="button"
                                data-testid="external-models-upload"
                                disabled={uploading}
                                onClick={() => fileRef.current?.click()}
                                title="Upload a .glb or .gltf into this collection. It is compressed on the way up."
                                className="shrink-0 rounded-sm border border-gray-600 px-2 py-0.5 text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-40"
                            >
                                {uploading ? "Uploading…" : "Upload…"}
                            </button>
                        </>
                    )}
                </div>
            </div>

            {bulk && (
                <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800">
                    <div className="flex-1 min-w-0">
                        <div className="text-[11px] text-gray-400">
                            Loading {bulk.done + 1} of {bulk.total}
                        </div>
                        <div className="truncate text-[11px] text-gray-500" title={bulk.name}>
                            {bulk.name}
                        </div>
                    </div>
                    {/* Stops after the model in flight rather than aborting it:
                        a half-loaded source in the scene is worse than one more
                        finished one. */}
                    <button
                        type="button"
                        className="shrink-0 rounded-sm border border-gray-600 px-2 py-0.5 text-[11px] text-gray-300 hover:bg-gray-800"
                        onClick={() => {
                            cancelBulk.current = true;
                        }}
                    >
                        Stop
                    </button>
                </div>
            )}

            {error && <div className="px-3 py-2 text-xs text-red-300">{error}</div>}

            {loading && <div className="px-3 py-4 text-center text-xs text-gray-500">Loading…</div>}

            {!loading && !binding && (
                <div className="px-3 py-4 text-xs text-gray-400">
                    No external collection is linked to this scope. An admin can link one under
                    Admin → External Models.
                </div>
            )}

            {!loading && binding && models.length === 0 && !error && (
                <div className="px-3 py-4 text-xs text-gray-400">
                    This collection has no loadable models.
                </div>
            )}

            {/* A scope whose filter hides everything is a configuration mistake
                worth saying out loud -- otherwise it is indistinguishable from
                an empty collection, and the two are fixed in different places. */}
            {!loading && binding && models.length > 0 && visibleModels.length === 0 && (
                <div className="px-3 py-4 text-xs text-amber-300">
                    All {models.length} models are hidden by this scope&rsquo;s filter
                    ({binding.hide.join(", ")}).
                </div>
            )}

            {/* Shown from two models up. Below that the filter is furniture, and
                the count line it carries would be saying "2 of 2". */}
            {!loading && binding && visibleModels.length > 1 && (
                <div className="px-3 py-2 border-b border-gray-800">
                    <input
                        type="search"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        data-testid="external-models-filter"
                        placeholder={`Filter ${visibleModels.length} model${visibleModels.length === 1 ? "" : "s"}…`}
                        aria-label="Filter external models"
                        className="w-full rounded-sm border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-100 placeholder:text-gray-500"
                    />
                    <div className="flex items-center gap-2 pt-1">
                        {query.trim() !== "" && (
                            <div className="text-[11px] text-gray-500 flex-1">
                                {shown.length} of {visibleModels.length}
                            </div>
                        )}
                        {query.trim() === "" && <div className="flex-1" />}
                        {/* Acts on what is SHOWN, not on everything. The filter is
                            how you say which ones you mean, so "load all" and
                            "load all of these" are the same button -- and with an
                            empty filter it does mean all, which is what the
                            confirmation is for. */}
                        <button
                            type="button"
                            data-testid="external-models-load-all"
                            disabled={bulk !== null || shown.length === 0}
                            onClick={() => void onLoadAll(shown)}
                            title={
                                bulk
                                    ? "A bulk load is already running"
                                    : "Load every model currently listed, one at a time"
                            }
                            className="shrink-0 rounded-sm border border-gray-600 px-2 py-0.5 text-[11px] text-gray-300 hover:bg-gray-800 disabled:opacity-40"
                        >
                            {(() => {
                                const todo = shown.filter((m) => !loaded.has(m.id)).length;
                                if (todo === 0) return "All loaded";
                                return `Load all (${todo})`;
                            })()}
                        </button>
                    </div>
                </div>
            )}

            {/* A filter that matches nothing must say so. An empty <ul> under a
                box you have just typed into reads as the panel having broken. */}
            {!loading && binding && visibleModels.length > 0 && shown.length === 0 && (
                <div className="px-3 py-4 text-xs text-gray-400">
                    No model matches “{query.trim()}”.
                </div>
            )}

            <ul>
                {shown.map((m) => {
                    const isLoaded = loaded.has(m.id);
                    return (
                        <li key={m.id} className="flex items-center gap-2 px-3 py-2 border-t border-gray-800">
                            {/* The description is the hover, not the label: a
                                provider puts the part that does not fit there
                                -- the source export a model came from,
                                which can be the same string on nearly every row.
                                Falls back to the name so a provider that sets
                                no description still gets a tooltip for a
                                truncated one. */}
                            <span
                                className="flex-1 truncate text-sm text-gray-100"
                                title={m.description ? `${m.name}\n${m.description}` : m.name}
                            >
                                {m.name}
                            </span>
                            <button
                                type="button"
                                className="text-xs px-2 py-1 rounded-sm border border-gray-700 text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                                disabled={busy === m.id}
                                onClick={() => (isLoaded ? onUnload(m) : void onLoad(m))}
                            >
                                {busy === m.id ? "…" : isLoaded ? "Unload" : "Load"}
                            </button>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
};

export default ExternalModelsPanel;
