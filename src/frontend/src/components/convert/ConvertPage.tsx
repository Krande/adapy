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

// Auto-pick the user's own scope when /convert mounts and the user
// hasn't explicitly chosen one yet. Mirrors the "auto-mint a
// personal scope on first visit" behaviour without needing a new
// server endpoint — the user scope is already in `me.scopes`.
function useEnsureUserScope(): void {
    const available = useScopeStore((s) => s.available);
    const current = useScopeStore((s) => s.current);
    const setCurrent = useScopeStore((s) => s.setCurrent);

    useEffect(() => {
        if (current && current.kind === "user") return;
        const userScope = available.find((s) => s.kind === "user");
        if (userScope) {
            setCurrent(userScope);
        }
    }, [available, current, setCurrent]);
}

export interface ConvertPageProps {
    /**
     * Rendered inside the running viewer (Convert mode) rather than at /convert.
     *
     * Only affects the "back to the viewer" link — see the header. Defaulting to false
     * keeps the standalone route's behaviour for every existing caller.
     */
    inViewer?: boolean;
}

const ConvertPage: React.FC<ConvertPageProps> = ({inViewer = false}) => {
    useEnsureUserScope();
    const rows = useConvertPageStore((s) => s.rows);
    const current = useScopeStore((s) => s.current);

    return (
        // ``h-full`` so the page adapts to its container — full-page
        // route wraps in ``h-[100dvh]``, in-viewer modal wraps in
        // an Rnd-sized container. ``overflow-y-auto`` so long
        // conversion lists scroll within the panel, not the page.
        <div className="h-full w-full bg-surface-0 text-content overflow-y-auto">
            <header className="border-b border-edge px-6 py-4 flex items-center justify-between gap-4 flex-wrap">
                <div className="flex items-baseline gap-3">
                    <h1 className="text-xl font-semibold">adapy converter</h1>
                    <span className="text-xs text-content-muted">
                        CAD &amp; FEA file conversion
                    </span>
                </div>
                <div className="flex items-center gap-3">
                    <WorkerStatusBadge/>
                    {/* "Back to the viewer" only on the standalone /convert route.
                    
                        In Convert MODE this page fills the viewport of the running
                        viewer, with the mode switcher directly above it — a link that
                        navigates the whole window away is both redundant and worse than
                        the control beside it, and it discards the session to get where
                        one click already goes. The page profile is a different case: it
                        is a deep link someone may have arrived at cold, with no switcher
                        anywhere, so there the link is the only way back. */}
                    {!inViewer && (
                        <a href="/" className="text-sm text-accent hover:text-accent">
                            ← back to viewer
                        </a>
                    )}
                </div>
            </header>

            <main className="max-w-3xl mx-auto px-6 py-8 space-y-6">
                <ConvertDropZone/>

                {current ? (
                    <div className="text-xs text-content-subtle">
                        files land in your personal scope (<span className="font-mono">{current.name}</span>) — they're visible from the main viewer too
                    </div>
                ) : (
                    <div className="text-xs text-warn">
                        Waiting for scope to load…
                    </div>
                )}

                {rows.length > 0 && (
                    <section className="space-y-2">
                        <h2 className="text-xs uppercase tracking-wider text-content-muted">
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
