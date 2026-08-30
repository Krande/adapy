import React, {useCallback, useEffect, useState} from "react";

import {makePluginContextStandalone} from "@/plugins";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {useExternalModelsStore} from "@/state/externalModelsStore";
import {
    ExternalModel,
    bindingFor,
    catalogueNonce,
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
    const [binding, setBinding] = useState<{provider: string; collection: string} | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [loaded, setLoaded] = useState<Set<string>>(new Set());
    const [busy, setBusy] = useState<string | null>(null);
    // Whether THIS provider accepts models. Declared by the provider, not
    // configured here, so a read-only catalogue never shows the control at all
    // rather than showing one that fails when pressed.
    const [uploadable, setUploadable] = useState(false);
    const [uploading, setUploading] = useState(false);
    const fileRef = React.useRef<HTMLInputElement | null>(null);

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

    const onLoad = useCallback(
        async (m: ExternalModel) => {
            if (!binding) return;
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
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setBusy(null);
            }
        },
        [binding, scope, sourceNameFor],
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

            <ul>
                {models.map((m) => {
                    const isLoaded = loaded.has(m.id);
                    return (
                        <li key={m.id} className="flex items-center gap-2 px-3 py-2 border-t border-gray-800">
                            <span className="flex-1 truncate text-sm text-gray-100" title={m.name}>{m.name}</span>
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
