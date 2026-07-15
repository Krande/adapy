// OIDC PKCE code-flow client. Provider-agnostic: works against
// Authentik (self-hosted) and Azure AD direct (enterprise) — both expose
// `.well-known/openid-configuration` and a token endpoint that accepts
// the standard PKCE exchange.
//
// Token storage trade-off (phase 1):
//   - Access token  → in-memory only (XSS hardens via shorter exposure)
//   - Refresh token → sessionStorage (survives reload-in-tab; gone on
//     tab-close)
//
// Closing the tab forces a fresh sign-in. A reload within the tab
// silently refreshes via the stored refresh token. We deliberately
// stay out of localStorage to avoid handing a long-lived credential
// to any future XSS.

import {runtime} from "@/runtime/config";

interface DiscoveryDoc {
    authorization_endpoint: string;
    token_endpoint: string;
    end_session_endpoint?: string;
}

interface TokenResponse {
    access_token: string;
    expires_in?: number;
    refresh_token?: string;
    id_token?: string;
    token_type?: string;
}

const STORAGE_PKCE = "ada-oidc-pkce";
const STORAGE_RETURN = "ada-oidc-return";
const STORAGE_REFRESH = "ada-oidc-refresh";
const STORAGE_STATE = "ada-oidc-state";
// Persistent cache of the OIDC discovery doc. The endpoints (authorize/token/
// jwks) are public and effectively static, so caching across page reloads
// removes a ~260ms authentik round-trip (plus a possible slow TLS handshake)
// from the token-refresh path that runs on every load. Keyed by issuer so a
// config change invalidates it; short-ish TTL so a genuine endpoint move is
// picked up within the day.
const STORAGE_DISCOVERY = "ada-oidc-discovery";
const DISCOVERY_TTL_MS = 24 * 60 * 60 * 1000;

let discovery: DiscoveryDoc | null = null;
let accessToken: string | null = null;
let accessTokenExpiry = 0;
let refreshToken: string | null = sessionStorage.getItem(STORAGE_REFRESH);
let userClaims: Record<string, unknown> | null = null;

// One inflight refresh. If many fetches hit a 401 simultaneously we
// don't want to fan out N concurrent token-refresh calls.
let refreshInflight: Promise<boolean> | null = null;

function redirectUri(): string {
    return `${window.location.origin}/auth/callback`;
}

function base64url(bytes: Uint8Array): string {
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    return btoa(s).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function randomUrl(byteLen: number): string {
    const arr = new Uint8Array(byteLen);
    crypto.getRandomValues(arr);
    return base64url(arr);
}

async function sha256Bytes(s: string): Promise<Uint8Array> {
    const buf = new TextEncoder().encode(s);
    const hash = await crypto.subtle.digest("SHA-256", buf);
    return new Uint8Array(hash);
}

async function loadDiscovery(): Promise<DiscoveryDoc> {
    if (discovery) return discovery;
    const issuer = runtime.authIssuer();
    if (!issuer) throw new Error("AUTH_ISSUER not configured");
    // Persistent cache (survives reloads), validated against the current issuer
    // and TTL. Wrapped in try/catch so a disabled/full localStorage (private
    // mode, embedded iframe) silently falls back to fetching.
    try {
        const raw = localStorage.getItem(STORAGE_DISCOVERY);
        if (raw) {
            const c = JSON.parse(raw) as {doc: DiscoveryDoc; at: number; issuer: string};
            if (c.doc && c.issuer === issuer && Date.now() - c.at < DISCOVERY_TTL_MS) {
                discovery = c.doc;
                return discovery;
            }
        }
    } catch {
        /* corrupt / unavailable cache — fall through and refetch */
    }
    const r = await fetch(`${issuer}/.well-known/openid-configuration`);
    if (!r.ok) throw new Error(`oidc discovery failed: ${r.status}`);
    discovery = await r.json();
    try {
        localStorage.setItem(STORAGE_DISCOVERY, JSON.stringify({doc: discovery, at: Date.now(), issuer}));
    } catch {
        /* storage unavailable — the in-memory module cache still applies */
    }
    return discovery!;
}

function decodeJwtClaims(jwt: string): Record<string, unknown> | null {
    // Phase 1: we trust the access token because the *server* verifies
    // it on every request. Decoding here is purely for display
    // (showing email / name in the user menu); never used for AuthZ.
    try {
        const parts = jwt.split(".");
        if (parts.length < 2) return null;
        const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
        const padded = payload + "===".slice(0, (4 - (payload.length % 4)) % 4);
        return JSON.parse(atob(padded));
    } catch {
        return null;
    }
}

function acceptTokenResponse(body: TokenResponse): void {
    accessToken = body.access_token;
    accessTokenExpiry = Date.now() + (body.expires_in ?? 300) * 1000;
    if (body.refresh_token) {
        refreshToken = body.refresh_token;
        sessionStorage.setItem(STORAGE_REFRESH, refreshToken);
    }
    // Prefer id_token for user claims (it's the canonical "who is the
    // user?" doc); fall back to the access token, which Authentik also
    // populates with email/name.
    userClaims = decodeJwtClaims(body.id_token || body.access_token);
}

function clearTokens(): void {
    accessToken = null;
    accessTokenExpiry = 0;
    refreshToken = null;
    userClaims = null;
    sessionStorage.removeItem(STORAGE_REFRESH);
}

export function isAuthEnabled(): boolean {
    return runtime.authEnabled();
}

export function isSignedIn(): boolean {
    if (!accessToken) return false;
    // 30s skew so we don't hand out an about-to-expire token to a
    // request that takes more than zero ms to ship.
    return Date.now() < accessTokenExpiry - 30_000;
}

export function getAccessToken(): string | null {
    return isSignedIn() ? accessToken : null;
}

export function getUser(): {sub?: string; email?: string; name?: string} {
    const c = userClaims || {};
    return {
        sub: typeof c.sub === "string" ? c.sub : undefined,
        email:
            (typeof c.email === "string" ? c.email : undefined) ||
            (typeof c.preferred_username === "string"
                ? (c.preferred_username as string)
                : undefined),
        name:
            (typeof c.name === "string" ? c.name : undefined) ||
            (typeof c.preferred_username === "string"
                ? (c.preferred_username as string)
                : undefined),
    };
}

/** Kick off the authorize redirect. Caller is the AuthGate UI. */
export async function signIn(
    returnUrl?: string,
    opts?: {forceLogin?: boolean},
): Promise<void> {
    const d = await loadDiscovery();
    const verifier = randomUrl(32);
    const challenge = base64url(await sha256Bytes(verifier));
    const state = randomUrl(16);
    sessionStorage.setItem(STORAGE_PKCE, verifier);
    sessionStorage.setItem(STORAGE_STATE, state);
    sessionStorage.setItem(
        STORAGE_RETURN,
        returnUrl || window.location.pathname + window.location.search,
    );
    // offline_access asks the IdP to issue a refresh_token. Both
    // Authentik and Azure honor it; without it reload-in-tab forces a
    // re-authorize round-trip. An optional configured scope is appended
    // for providers (e.g. Azure AD) that otherwise mint an access token
    // audienced at something other than this API — requesting the API's
    // own scope (api://<client-id>/access_as_user) makes the issued
    // token's `aud` match the API. Empty (Authentik) → base scope only.
    const baseScope = "openid profile email offline_access";
    const extraScope = runtime.authScope();
    const params = new URLSearchParams({
        response_type: "code",
        client_id: runtime.authClientId(),
        redirect_uri: redirectUri(),
        scope: extraScope ? `${baseScope} ${extraScope}` : baseScope,
        code_challenge: challenge,
        code_challenge_method: "S256",
        state,
    });
    if (opts?.forceLogin) {
        // Standard OIDC prompt param — both Authentik and Azure AD honor
        // it by re-showing the login form even when an IdP session
        // exists, letting the user pick a different account. Only sent
        // on explicit request so the normal silent-SSO path is unchanged.
        params.set("prompt", "login");
    }
    const aud = runtime.authAudience();
    if (aud && aud !== runtime.authClientId()) {
        // Auth0 / some Azure AD configurations need this so the issued
        // access token has the right `aud` claim. Authentik ignores
        // unknown params, so it's safe to always send when set.
        params.set("audience", aud);
    }
    window.location.assign(`${d.authorization_endpoint}?${params}`);
}

/** Handle the redirect-back URL. Returns the original return path. */
export async function completeSignIn(): Promise<string> {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    const expectedState = sessionStorage.getItem(STORAGE_STATE);
    sessionStorage.removeItem(STORAGE_STATE);
    if (!code) throw new Error("no auth code in callback URL");
    if (!expectedState || state !== expectedState) {
        throw new Error("state mismatch — possible CSRF, refusing to sign in");
    }
    const verifier = sessionStorage.getItem(STORAGE_PKCE);
    sessionStorage.removeItem(STORAGE_PKCE);
    if (!verifier) throw new Error("no PKCE verifier (sessionStorage cleared?)");
    const d = await loadDiscovery();
    const params = new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: redirectUri(),
        client_id: runtime.authClientId(),
        code_verifier: verifier,
    });
    const r = await fetch(d.token_endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body: params.toString(),
    });
    if (!r.ok) {
        throw new Error(`token exchange failed: ${r.status} ${await r.text()}`);
    }
    acceptTokenResponse(await r.json());
    const ret = sessionStorage.getItem(STORAGE_RETURN) || "/";
    sessionStorage.removeItem(STORAGE_RETURN);
    return ret;
}

/** Refresh the access token using the stored refresh token. Returns
 *  whether a usable token is now available. */
export async function refreshAccessToken(): Promise<boolean> {
    if (!refreshToken) return false;
    if (refreshInflight) return refreshInflight;
    refreshInflight = (async () => {
        try {
            const d = await loadDiscovery();
            const r = await fetch(d.token_endpoint, {
                method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: new URLSearchParams({
                    grant_type: "refresh_token",
                    refresh_token: refreshToken!,
                    client_id: runtime.authClientId(),
                }).toString(),
            });
            if (!r.ok) {
                clearTokens();
                return false;
            }
            acceptTokenResponse(await r.json());
            return true;
        } catch {
            clearTokens();
            return false;
        } finally {
            refreshInflight = null;
        }
    })();
    return refreshInflight;
}

/** Top-level sign-out: clears local state and redirects via the IdP's
 *  end-session endpoint when available, else just to /. */
export async function signOut(): Promise<void> {
    clearTokens();
    try {
        const d = await loadDiscovery();
        if (d.end_session_endpoint) {
            window.location.assign(d.end_session_endpoint);
            return;
        }
    } catch {
        /* discovery may fail offline — just go home */
    }
    window.location.assign("/");
}

/** Best-effort warm-up on app boot: if a refresh token is stashed in
 *  sessionStorage, swap it for an access token before first render so
 *  the user doesn't see a flicker through the auth gate. */
export async function bootstrap(): Promise<void> {
    if (!isAuthEnabled()) return;
    if (refreshToken && !isSignedIn()) {
        await refreshAccessToken();
    }
}
