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
// Treat a token as expired this long before it actually is, so we never hand
// out an about-to-expire token to a request that takes more than zero ms to
// ship. Applies to the primary token and to every per-resource token alike.
const EXPIRY_SKEW_MS = 30_000;

let discovery: DiscoveryDoc | null = null;
let accessToken: string | null = null;
let accessTokenExpiry = 0;
let refreshToken: string | null = sessionStorage.getItem(STORAGE_REFRESH);
let userClaims: Record<string, unknown> | null = null;

// One inflight refresh. If many fetches hit a 401 simultaneously we
// don't want to fan out N concurrent token-refresh calls.
let refreshInflight: Promise<boolean> | null = null;

// Access tokens minted for a *different* resource than this app's own API
// (see getAccessTokenForScope). Kept apart from `accessToken` because an
// OAuth access token carries exactly one `aud` — these are not
// interchangeable with the primary one, so they get their own cache and
// their own expiry. Keyed by the requested scope string.
const scopedTokens = new Map<string, {token: string; expiry: number}>();
// Same idea as `refreshInflight`, one entry per scope: N concurrent callers
// asking for the same resource produce one token request, not N.
const scopedInflight = new Map<string, Promise<string>>();

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
    // Per-resource tokens were minted off the session we are dropping, so
    // they die with it. The inflight map is cleared too — those requests
    // will fail (or resolve into a cache we no longer trust), and leaving
    // their entries behind would hand a post-sign-out caller a token from
    // the previous session.
    scopedTokens.clear();
    scopedInflight.clear();
    sessionStorage.removeItem(STORAGE_REFRESH);
}

export function isAuthEnabled(): boolean {
    return runtime.authEnabled();
}

export function isSignedIn(): boolean {
    if (!accessToken) return false;
    return Date.now() < accessTokenExpiry - EXPIRY_SKEW_MS;
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
export interface SignInPrompt {
    /** Re-authenticate: the IdP asks for credentials again even with a live
     *  session. For recovering from a session that is broken rather than for
     *  changing who is signed in. */
    forceLogin?: boolean;
    /** Show the IdP's account chooser. This is the "switch user" case: an
     *  account already signed in at the IdP can be picked without re-entering
     *  credentials, and a new one can be added. `login` would force the
     *  password prompt instead, which is a worse fit for switching. */
    selectAccount?: boolean;
}

/** The OIDC `prompt` value for these options, or null to send none.
 *
 *  Omitted by default so the normal silent-SSO path is untouched — a `prompt`
 *  on every authorize would defeat SSO. `select_account` wins when both are
 *  set: it is the more specific request, and both Authentik and Azure AD
 *  honour it.
 */
export function promptParam(opts?: SignInPrompt): string | null {
    if (opts?.selectAccount) return "select_account";
    if (opts?.forceLogin) return "login";
    return null;
}

export async function signIn(
    returnUrl?: string,
    opts?: SignInPrompt,
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
    const prompt = promptParam(opts);
    if (prompt) params.set("prompt", prompt);
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

/** Raised when a per-resource token acquisition was attempted and failed:
 *  the provider refused the scope, answered without an access token, or the
 *  request never completed. Distinct from the `null` return of
 *  {@link getAccessTokenForScope}, which means "there was no session to
 *  exchange in the first place". */
export class ScopedTokenError extends Error {
    /** Status from the token endpoint, when the failure came with a response. */
    readonly status?: number;

    constructor(message: string, status?: number) {
        super(message);
        this.name = "ScopedTokenError";
        this.status = status;
    }
}

/** Take a rotated refresh token out of a token response — and nothing else.
 *
 *  Providers that rotate refresh tokens invalidate the one just redeemed, so
 *  dropping the replacement would break the *primary* session at its next
 *  refresh. We therefore adopt it. What we deliberately do not adopt is the
 *  rest of the response: its access token, expiry and claims describe the
 *  other resource, and writing them into `accessToken` / `accessTokenExpiry`
 *  / `userClaims` would leave the app holding a token the API it talks to
 *  will reject. (Note the inherent race with rotation: a per-resource
 *  acquisition running concurrently with a primary refresh means two
 *  redemptions of the same refresh token, and a rotating provider will
 *  invalidate one of them. That is a property of rotation itself, not
 *  something this function can paper over — the loser falls back to a
 *  re-authorize round-trip.) */
function adoptRotatedRefreshToken(body: TokenResponse): void {
    if (!body.refresh_token || body.refresh_token === refreshToken) return;
    refreshToken = body.refresh_token;
    sessionStorage.setItem(STORAGE_REFRESH, refreshToken);
}

/** One acquisition attempt: redeem the refresh token for `scope`. Kept apart
 *  from the public entry point so that caching, single-flight bookkeeping and
 *  the network exchange stay separable. Always rejects with a
 *  {@link ScopedTokenError}, never with a raw transport failure. */
async function requestScopedToken(scope: string, rt: string): Promise<string> {
    try {
        const d = await loadDiscovery();
        const r = await fetch(d.token_endpoint, {
            method: "POST",
            headers: {"Content-Type": "application/x-www-form-urlencoded"},
            body: new URLSearchParams({
                grant_type: "refresh_token",
                refresh_token: rt,
                client_id: runtime.authClientId(),
                scope,
            }).toString(),
        });
        if (!r.ok) {
            // Note: no clearTokens() here, unlike refreshAccessToken. A
            // provider refusing *this* scope says nothing about the session;
            // signing the user out over it would be wrong.
            throw new ScopedTokenError(
                `identity provider refused scope "${scope}": ${r.status}`,
                r.status,
            );
        }
        const body = (await r.json()) as TokenResponse;
        if (typeof body.access_token !== "string" || !body.access_token) {
            throw new ScopedTokenError(
                `token response for scope "${scope}" carried no access_token`,
            );
        }
        // Note the absence of acceptTokenResponse(): everything in this
        // response except a rotated refresh token belongs to the other
        // resource and must stay out of the primary session's state.
        adoptRotatedRefreshToken(body);
        scopedTokens.set(scope, {
            token: body.access_token,
            expiry: Date.now() + (body.expires_in ?? 300) * 1000,
        });
        return body.access_token;
    } catch (e) {
        if (e instanceof ScopedTokenError) throw e;
        // Discovery failure, transport error, unparseable body — wrapped so
        // nothing raw escapes this module.
        throw new ScopedTokenError(
            `could not acquire a token for scope "${scope}": ${
                e instanceof Error ? e.message : String(e)
            }`,
        );
    }
}

/** Get an access token for a resource other than this app's own API.
 *
 *  Why a second token is needed at all: an OAuth access token carries
 *  exactly one `aud`. The token obtained at sign-in is audienced at this
 *  app's API — that is precisely what the configured extra scope is for (see
 *  `signIn`) — so it cannot also be presented to a different API that
 *  validates its own audience. A plugin or feature calling such an API must
 *  therefore acquire its own token; the two coexist rather than replace one
 *  another.
 *
 *  Mechanism: redeem the stored refresh token at the token endpoint with the
 *  requested `scope`. That is an ordinary OAuth 2 refresh-token grant with a
 *  changed scope, and providers that support it answer with an access token
 *  for the other resource. This never disturbs the primary token.
 *
 *  Failure modes, deliberately split:
 *    - Returns `null` when there is nothing to exchange — auth is disabled
 *      for this deployment, or the user is not signed in (no stored refresh
 *      token: never signed in, or signed out).
 *    - Throws {@link ScopedTokenError} when an exchange was attempted and
 *      failed — the provider refused the scope, replied without an access
 *      token, or the request never completed. Transport rejections are
 *      wrapped, so a caller only ever sees `null` or a ScopedTokenError,
 *      never a bare fetch rejection and never an empty string.
 *
 *  Tokens are cached per scope with their own expiry (same skew as the
 *  primary token), and concurrent callers asking for the same scope share a
 *  single token request. */
export async function getAccessTokenForScope(scope: string): Promise<string | null> {
    const key = scope.trim();
    if (!key) {
        throw new ScopedTokenError("getAccessTokenForScope needs a non-empty scope");
    }
    if (!isAuthEnabled()) return null;
    // Keyed off the refresh token rather than isSignedIn(): that reports on
    // the *primary* access token's freshness, which is beside the point here.
    // A session can be perfectly alive with a stale primary token, and this
    // exchange only ever needs the refresh token.
    const rt = refreshToken;
    if (!rt) return null;

    const cached = scopedTokens.get(key);
    if (cached && Date.now() < cached.expiry - EXPIRY_SKEW_MS) return cached.token;

    const pending = scopedInflight.get(key);
    if (pending) return pending;

    const inflight = requestScopedToken(key, rt);
    scopedInflight.set(key, inflight);
    // Retire the entry once it settles, but only if it is still ours:
    // clearTokens() may have wiped the map and a later caller may already
    // have registered a fresh request. The trailing catch is only there
    // because `.finally()` returns a *derived* promise — without it a
    // failed acquisition would surface as an unhandled rejection even
    // though the caller handles the promise we return.
    void inflight
        .finally(() => {
            if (scopedInflight.get(key) === inflight) scopedInflight.delete(key);
        })
        .catch(() => {});
    return inflight;
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
