import React, {useEffect, useState} from "react";
import {completeSignIn} from "@/services/auth/oidc";

// Minimal landing for the OIDC redirect_uri. Exchanges the auth code,
// then bounces back to whatever the user was looking at before the
// sign-in detour.
const AuthCallback: React.FC = () => {
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        completeSignIn()
            .then((returnUrl) => {
                // Replace, not push: don't leave the ?code=... URL in
                // browser history where a back-button could re-trigger
                // the (now-spent) authorization code.
                window.history.replaceState({}, "", returnUrl);
                window.location.assign(returnUrl);
            })
            .catch((err) => {
                console.error("sign-in failed", err);
                setError(String(err?.message || err));
            });
    }, []);

    return (
        <div className="flex h-full w-full items-center justify-center bg-surface-0 text-white text-sm">
            {error ? (
                <div className="max-w-md rounded-sm bg-fail-subtle p-3 space-y-2">
                    <div className="font-bold">Sign-in failed</div>
                    <div className="font-mono text-xs whitespace-pre-wrap">{error}</div>
                    <button
                        className="bg-accent pointer-fine:hover:bg-accent-hover px-3 py-1 rounded-sm text-xs"
                        onClick={() => window.location.assign("/")}
                    >
                        Back home
                    </button>
                </div>
            ) : (
                <div>Completing sign-in…</div>
            )}
        </div>
    );
};

export default AuthCallback;
