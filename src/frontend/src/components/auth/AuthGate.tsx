import React, {useEffect, useState} from "react";
import {bootstrap, isAuthEnabled, isSignedIn, signIn} from "@/services/auth/oidc";

// Gates the REST-mode app behind a verified bearer token. When auth
// is disabled (default in dev / desktop) it's a transparent
// pass-through. When enabled and there's no usable token, shows a
// minimal sign-in prompt instead of the app.
//
// The bootstrap call attempts a silent token refresh from the stashed
// refresh token (sessionStorage) so reload-in-tab doesn't always
// bounce through the IdP.
const AuthGate: React.FC<{children: React.ReactNode}> = ({children}) => {
    const enabled = isAuthEnabled();
    const [ready, setReady] = useState(!enabled);
    const [signedIn, setSignedIn] = useState(!enabled || isSignedIn());

    useEffect(() => {
        if (!enabled) return;
        let cancelled = false;
        bootstrap()
            .catch(() => {/* refresh failed → show the sign-in button */})
            .finally(() => {
                if (cancelled) return;
                setSignedIn(isSignedIn());
                setReady(true);
            });
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
