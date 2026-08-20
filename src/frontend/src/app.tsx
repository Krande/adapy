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
const isRestMode = runtime.isRestMode();
const isSimFollower = !!simFollowerParams();
const isAuthCallback = isRestMode && window.location.pathname === "/auth/callback";
const isConvertPage = isRestMode && window.location.pathname.startsWith("/convert");
const isAdminPage = isRestMode && window.location.pathname.startsWith("/admin");

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
                <AppShell profile="viewer" />
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
        // A follower window boots controls-only: the Simulation plugin tab
        // full-window, no 3D canvas / tree / websocket. Under the provider so the
        // tab's stores resolve; behind AuthGate in REST mode so plugin API calls
        // (e.g. enqueuing a check) carry the caller's token.
        const page = (
            <Suspense fallback={null}>
                <SimFollowerPage/>
            </Suspense>
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

    // Everything that touches viewer state lives under the provider so
    // Phase-2 migrations can flip consumers off the module-level
    // singletons in state/refs.ts and state/*Store.ts without touching
    // this file.
    // Unreachable: the viewer branch above claims everything the four page branches do
    // not. An explicit null rather than nothing, so a route added later without its own
    // return does not fall off the end of the function and render undefined.
    return null;
}

export default App;