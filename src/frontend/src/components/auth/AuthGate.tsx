import React, {useEffect, useState} from "react";
import {bootstrap, isAuthEnabled, isSignedIn, signIn} from "@/services/auth/oidc";
import {viewerApi} from "@/services/viewerApi";
import {useScopeStore} from "@/state/scopeStore";
import {useMeStore} from "@/state/meStore";
import {loadInitialServerState} from "@/utils/websocket/initWebSocket";

// Gates the REST-mode app behind a verified bearer token. When auth
// is disabled (default in dev / desktop) it's a transparent
// pass-through. When enabled and there's no usable token, shows a
// minimal sign-in prompt instead of the app.
//
// The bootstrap call attempts a silent token refresh from the stashed
// refresh token (sessionStorage) so reload-in-tab doesn't always
// bounce through the IdP. After auth resolves we also fetch /api/me
// and populate the scope + me stores so the project picker and admin
// button have data immediately.
async function loadMe(): Promise<void> {
    try {
        const me = await viewerApi.me();
        useScopeStore.getState().setAvailable(me.scopes);
        useMeStore.getState().set({
            sub: me.sub,
            email: me.email,
            displayName: me.displayName,
            isAdmin: me.isAdmin,
        });
    } catch (err) {
        console.warn("failed to load /api/me", err);
    }
}

const AuthGate: React.FC<{children: React.ReactNode}> = ({children}) => {
    const enabled = isAuthEnabled();
    const [ready, setReady] = useState(!enabled);
    const [signedIn, setSignedIn] = useState(!enabled || isSignedIn());

    useEffect(() => {
        let cancelled = false;
        const finish = async () => {
            if (cancelled) return;
            const live = isSignedIn() || !enabled;
            setSignedIn(live);
            setReady(true);
            // Load /api/me whenever we have something to talk to: either
            // a valid token (auth on) or the synthetic local-dev user
            // (auth off). Skip when the sign-in prompt is being shown.
            if (live) {
                await loadMe();
                // initWebSocket's onConnect skipped the legacy RPCs
                // when auth is enabled and no token was in hand yet
                // (REST onConnect fires during initWebSocket, before
                // bootstrap resolves). Now that we have a token,
                // ship them once. Auth-disabled mode already fired
                // them from initWebSocket — don't double-fire.
                if (enabled) loadInitialServerState();
            }
        };
        if (!enabled) {
            void finish();
            return () => { cancelled = true; };
        }
        bootstrap()
            .catch(() => {/* refresh failed → show the sign-in button */})
            .finally(finish);
        return () => {
            cancelled = true;
        };
    }, [enabled]);

    if (!enabled || signedIn) return <>{children}</>;
    if (!ready) {
        return (
            <div className="flex h-full w-full items-center justify-center bg-gray-900 text-white text-sm">
                Loading…
            </div>
        );
    }

    return (
        <div className="flex h-full w-full items-center justify-center bg-gray-900 text-white">
            <div className="max-w-sm rounded bg-gray-800 p-6 space-y-4 shadow-xl">
                <h1 className="text-lg font-bold">ada viewer</h1>
                <p className="text-sm text-gray-300">
                    Sign in with your organisation account to continue.
                </p>
                <button
                    className="bg-blue-700 hover:bg-blue-600 px-4 py-2 rounded text-white w-full"
                    onClick={() => {
                        void signIn();
                    }}
                >
                    Sign in
                </button>
            </div>
        </div>
    );
};

export default AuthGate;
