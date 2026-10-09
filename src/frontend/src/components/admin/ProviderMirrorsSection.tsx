import React, {useCallback, useEffect, useState} from "react";

import {catalogueNonce, listCollectionsDetailed, listProviders} from "@/services/externalModels";
import type {ExternalModelProvider} from "@/services/externalModels";

import MirrorPanel from "./MirrorPanel";

// Providers tab section: the upstream catalogue mirrors.
//
// Some providers copy an upstream catalogue into this deployment's own object
// store and serve it from there (see MirrorPanel's header for why). This lists
// every provider that says it does, and nothing else -- a provider that serves
// its upstream directly has no mirror to show.
//
// It used to sit at the bottom of the External Models tab, beside the scope
// bindings. The bindings are gone (the provider system's per-scope collections
// replaced them); the mirrors are a property of a provider, so they live with
// the providers.

const CATALOGUE_SCOPE = "shared";

const ProviderMirrorsSection: React.FC = () => {
    const [mirrors, setMirrors] = useState<ExternalModelProvider[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Cache-busting token: without it the catalogue reads cache-hit forever and a
    // provider configured after the first read never shows up.
    const [nonce, setNonce] = useState(catalogueNonce);

    const refresh = useCallback(async () => {
        setError(null);
        try {
            const providers = await listProviders(CATALOGUE_SCOPE, {refresh: nonce});
            // A provider reports whether it mirrors in its collection listing, so
            // this costs the round trip the old tab already made per provider.
            const probed = await Promise.all(
                providers.map(async (p) => {
                    try {
                        const out = await listCollectionsDetailed(p.id, CATALOGUE_SCOPE, {refresh: nonce});
                        return out.canMirror ? p : null;
                    } catch {
                        return null;
                    }
                }),
            );
            setMirrors(probed.filter((p): p is ExternalModelProvider => p !== null));
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
            setMirrors([]);
        }
    }, [nonce]);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    // Nothing to say on a deployment without a mirroring provider: most have none.
    if (mirrors !== null && mirrors.length === 0 && !error) return null;

    return (
        <section className="border-b border-gray-800" data-testid="provider-mirrors-admin">
            <div className="px-3 pt-3 pb-1 space-y-1">
                <div className="flex items-center gap-2">
                    <div className="text-sm font-medium flex-1">Upstream mirrors</div>
                    <button
                        type="button"
                        className="text-xs px-2 py-1 rounded-sm border border-gray-700 hover:bg-gray-800"
                        onClick={() => setNonce(catalogueNonce())}
                    >
                        Refresh
                    </button>
                </div>
                <div className="text-xs text-gray-400">
                    Providers that copy an upstream catalogue into this deployment's object store and serve it
                    from there: what each copy holds, and whether it is refreshed.
                </div>
            </div>
            {error && <div className="px-3 py-2 text-xs text-red-300">{error}</div>}
            {mirrors === null ? (
                <div className="px-3 py-3 text-xs text-gray-500">Checking providers…</div>
            ) : (
                mirrors.map((p) => <MirrorPanel key={p.id} provider={p.id}/>)
            )}
        </section>
    );
};

export default ProviderMirrorsSection;
