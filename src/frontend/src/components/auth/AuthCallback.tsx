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
        <div className="flex h-full w-full items-center justify-center bg-gray-900 text-white text-sm">
            {error ? (
                <div className="max-w-md rounded bg-red-900/40 p-3 space-y-2">
                    <div className="font-bold">Sign-in failed</div>
                    <div className="font-mono text-xs whitespace-pre-wrap">{error}</div>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-xs"
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
