import React, {useEffect} from "react";
import ConvertDropZone from "./ConvertDropZone";
import ConversionRow from "./ConversionRow";
import ExistingFilesPanel from "./ExistingFilesPanel";
import WorkerStatusBadge from "./WorkerStatusBadge";
import {useConvertPageStore} from "@/state/convertPageStore";
import {useScopeStore} from "@/state/scopeStore";

// Standalone CAD/FEA conversion page mounted at /convert. Lives
// outside `AdaViewerProvider` so the 3D canvas, scene graph, and
// websocket plumbing never get a chance to spin up — cold load is
// just the upload + convert plumbing and a Tailwind layout. Auth +
// scope bootstrap come from `AuthGate` upstream; this component
// assumes a current scope is selectable from `useScopeStore`.

// Auto-pick the user's own scope when the standalone /convert page mounts and the
// user hasn't explicitly chosen one yet. Mirrors the "auto-mint a personal scope on
// first visit" behaviour without needing a new server endpoint — the user scope is
// already in `me.scopes`.
//
// Not inside a running viewer (`inViewer`): there the scope is the viewer's, picked by
// the user or by the link that opened it (`?scope=shared`), and the Storage list was
// fetched for it. Switching it here behind the list's back left Storage listing one
// scope while loads went to the other - a file shown in Storage that 404s when opened.
function useEnsureUserScope(enabled: boolean): void {
    const available = useScopeStore((s) => s.available);
    const current = useScopeStore((s) => s.current);
    const setCurrent = useScopeStore((s) => s.setCurrent);

    useEffect(() => {
        if (!enabled) return;
        if (current && current.kind === "user") return;
        const userScope = available.find((s) => s.kind === "user");
        if (userScope) {
            setCurrent(userScope);
        }
    }, [enabled, available, current, setCurrent]);
}

export interface ConvertBodyProps {
    /** Mounted inside a running viewer: no "back to viewer" link, which would navigate
     *  the window away from it. Default false. */
    inViewer?: boolean;
}

const ConvertPage: React.FC<ConvertBodyProps> = ({inViewer = false}) => {
    useEnsureUserScope(!inViewer);
    const rows = useConvertPageStore((s) => s.rows);
    const current = useScopeStore((s) => s.current);

    return (
        // ``h-full`` so the page adapts to its container — full-page
        // route wraps in ``h-[100dvh]``, in-viewer modal wraps in
        // an Rnd-sized container. ``overflow-y-auto`` so long
        // conversion lists scroll within the panel, not the page.
        <div className="h-full w-full bg-gray-900 text-gray-100 overflow-y-auto">
            <header className="border-b border-gray-800 px-6 py-4 flex items-center justify-between gap-4 flex-wrap">
                <div className="flex items-baseline gap-3">
                    <h1 className="text-xl font-semibold">adapy converter</h1>
                    <span className="text-xs text-gray-400">
                        CAD &amp; FEA file conversion
                    </span>
                </div>
                <div className="flex items-center gap-3">
                    <WorkerStatusBadge/>
                    {!inViewer && (
                    <a
                        href="/"
                        className="text-sm text-blue-400 hover:text-blue-300"
                    >
                        ← back to viewer
                    </a>
                    )}
                </div>
            </header>

            <main className="max-w-3xl mx-auto px-6 py-8 space-y-6">
                <ConvertDropZone/>

                {current ? (
                    <div className="text-xs text-gray-500">
                        files land in your personal scope (<span className="font-mono">{current.name}</span>) — they're visible from the main viewer too
                    </div>
                ) : (
                    <div className="text-xs text-amber-400">
                        Waiting for scope to load…
                    </div>
                )}

                {rows.length > 0 && (
                    <section className="space-y-2">
                        <h2 className="text-xs uppercase tracking-wider text-gray-400">
                            Uploads &amp; conversions
                        </h2>
                        <div className="space-y-2">
                            {rows.map((row) => (
                                <ConversionRow key={row.sourceKey} row={row}/>
                            ))}
                        </div>
                    </section>
                )}

                <ExistingFilesPanel/>
            </main>
        </div>
    );
};

export default ConvertPage;
