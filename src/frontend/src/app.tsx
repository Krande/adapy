// src/App.tsx
import "./app.css";
import React, {useEffect, Suspense} from 'react'
import {runtime} from "@/runtime/config";

import {AdaViewerProvider} from "./state/AdaViewerContext";
import {followerParams as simFollowerParams} from "@/utils/simChannel";

// REST-only UI lives in its own chunk so the embedded desktop bundle
// (the index.zip shipped with ada-py) doesn't pull in the conversion /
// upload / Pyodide / OIDC code. The chunk only loads when
// COMMS_MODE === "rest".
const AuthGate = React.lazy(() => import("./components/auth/AuthGate"));
const AuthCallback = React.lazy(() => import("./components/auth/AuthCallback"));
const ConvertPage = React.lazy(() => import("./components/convert/ConvertPage"));
const AdminPanel = React.lazy(() => import("./components/admin/AdminPanel"));
// Canvas-less Simulation follower window (`?simfollow=…`) — mounted in its own
// lazy chunk so a normal viewer tab never pulls it in.
const SimFollowerPage = React.lazy(() => import("./components/simulation/SimFollowerPage"));
// Only pulled in on the graph profile; the viewer's node editor is a lazy dock panel.
const NodeEditorComponent = React.lazy(() => import("./components/node_editor/NodeEditorComponent"));
const isRestMode = runtime.isRestMode();
const isSimFollower = !!simFollowerParams();
const isAuthCallback = isRestMode && window.location.pathname === "/auth/callback";
const isConvertPage = isRestMode && window.location.pathname.startsWith("/convert");
const isAdminPage = isRestMode && window.location.pathname.startsWith("/admin");
// Inventory row A6. NODE_EDITOR_ONLY replaced the whole canvas with the node editor in
// the classic app; the shell defined a "graph" profile for it and then nothing ever
// mounted that profile — the flag was still read inside NodeEditorComponent, so the
// dead route left no error behind, just a viewer that ignored the flag.
const isNodeEditorOnly = runtime.nodeEditorOnly();

// Design-system catalogue at `?uikit=1`. Dev-only (import.meta.env.DEV is statically
// replaced, so rollup drops both the flag and the lazy chunk from every production
// build) and mounted before every other branch, since it needs no comms, no scene and
// no auth. Reviewing it cannot affect the real app.
const UiGallery = React.lazy(() => import("./components/ui/__gallery__/UiGallery"));
const isUiGallery =
    import.meta.env.DEV && new URLSearchParams(window.location.search).get("uikit") === "1";

// The new shell, opt-in behind ?shell=1 (remembered afterwards) while it is built out.
// The classic UI below stays the default and is completely untouched by this branch —
// the two never render at once, so reviewing the shell cannot regress the product.
// Resolved once at module load so the choice cannot flip mid-session.
const AppShell = React.lazy(() => import("./shell/AppShell"));


function App() {
    if (isUiGallery) {
        return (
            <Suspense fallback={null}>
                <UiGallery/>
            </Suspense>
        );
    }

    // The viewer. One layout now — the classic chrome is gone.
    //
    // AuthGate is not optional in REST mode: besides the sign-in gate it is what calls
    // /api/me and populates the scope store. Without it the shell boots with no scopes
    // and no admin flag, so the scope picker renders nothing and every scoped request
    // uses a default.
    if (!isAuthCallback && !isConvertPage && !isAdminPage && !isSimFollower) {
        const shell = (
            <Suspense fallback={null}>
                {isNodeEditorOnly ? (
                    // The graph profile: same shell, no three.js, ReactFlow in the
                    // viewport track. Rail and docks stay — the node editor is a workspace
                    // in its own right, not a bare canvas, and the menu bar is how you
                    // reach anything at all in a window with no mode switcher.
                    <AppShell
                        profile="graph"
                        pageTitle="Node editor"
                        viewportOverride={
                            <Suspense fallback={null}>
                                <NodeEditorComponent />
                            </Suspense>
                        }
                    />
                ) : (
                    <AppShell profile="viewer" />
                )}
            </Suspense>
        );
        return (
            <AdaViewerProvider>
                {isRestMode ? (
                    <Suspense fallback={null}>
                        <AuthGate>{shell}</AuthGate>
                    </Suspense>
                ) : (
                    shell
                )}
            </AdaViewerProvider>
        );
    }

    if (isAuthCallback) {
        // Dedicated landing for OIDC redirect_uri. Doesn't render the
        // viewer at all — it just exchanges the code and bounces back.
        // Stays outside the AdaViewerProvider since it never touches
        // viewer state.
        return (
            <Suspense fallback={null}>
                <AuthCallback/>
            </Suspense>
        );
    }

    if (isConvertPage) {
        // Standalone /convert, now inside the shell on the page profile.
        //
        // Still outside AdaViewerProvider, and the profile still says canvas: false — so
        // the 3D scene, the websocket and the tree never spin up, and ThreeCanvas stays
        // out of this route's entry chunk. That was the reason these pages were separate
        // in the first place and it has not been given up; only the dead end has.
        //
        // The page fills the viewport track via viewportOverride, which is the same slot
        // the graph profile uses for ReactFlow.
        return (
            <Suspense fallback={null}>
                <AuthGate>
                    <AppShell
                        profile="page"
                        pageTitle="Convert files"
                        viewportOverride={
                            <Suspense fallback={null}>
                                <ConvertPage/>
                            </Suspense>
                        }
                    />
                </AuthGate>
            </Suspense>
        );
    }

    if (isAdminPage) {
        // Path-mounted /admin, on the same page profile as /convert and for the same
        // reasons. AuthGate gives the panel the user object so it can render its
        // admin-only message rather than a blank screen; tab state still syncs to the URL
        // hash, so a refresh stays on /admin#<tab>.
        return (
            <Suspense fallback={null}>
                <AuthGate>
                    <AppShell
                        profile="page"
                        pageTitle="Administration"
                        viewportOverride={
                            <Suspense fallback={null}>
                                <AdminPanel/>
                            </Suspense>
                        }
                    />
                </AuthGate>
            </Suspense>
        );
    }

    if (isSimFollower) {
        // A follower window (?simfollow=…) on the shell's window profile: a thin title
        // bar naming what it follows, and the panel filling the rest.
        //
        // Canvas-less, like the pages — no 3D, no tree, no websocket — but unlike them it
        // offers no way "back", because there is nowhere back to. A follower belongs to
        // the tab that opened it and drives that tab's scene over the ada-sim
        // BroadcastChannel; navigating it to "/" would not return anywhere, it would
        // quietly promote it to a second full viewer against the same session.
        //
        // The title earns its place: these windows are opened several at a time, one per
        // source, and until now they were indistinguishable in the taskbar.
        const follow = simFollowerParams();
        const page = (
            <AppShell
                profile="window"
                pageTitle={follow ? `Following ${follow.source}` : "Simulation follower"}
                viewportOverride={
                    <Suspense fallback={null}>
                        <SimFollowerPage/>
                    </Suspense>
                }
            />
        );
        return (
            <AdaViewerProvider>
                {isRestMode ? (
                    <Suspense fallback={null}>
                        <AuthGate>{page}</AuthGate>
                    </Suspense>
                ) : (
                    page
                )}
            </AdaViewerProvider>
        );
    }

    // Unreachable: the viewer branch above claims everything the four page branches do
    // not. An explicit null rather than nothing, so a route added later without its own
    // return does not fall off the end of the function and render undefined.
    return null;
}

export default App;