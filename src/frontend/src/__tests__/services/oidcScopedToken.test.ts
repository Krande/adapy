import assert from "node:assert/strict";
import {test} from "node:test";

// Focused tests for the per-resource token acquisition in the OIDC client
// (getAccessTokenForScope). The browser globals the module touches and the
// token endpoint itself are both stubbed here. A real end-to-end exchange
// needs a live identity provider and cannot be unit-tested; what is covered
// below is everything this module actually controls — the request it sends,
// the caching / single-flight / isolation rules around it, and how it
// reports failure.

type OidcModule = typeof import("../../services/auth/oidc");

const ISSUER = "https://idp.example.test";
const TOKEN_ENDPOINT = `${ISSUER}/oauth/token`;
const STORAGE_REFRESH = "ada-oidc-refresh";
const OTHER_SCOPE = "other-api.read";

function fakeStorage(seed: Record<string, string> = {}): Storage {
    const m = new Map<string, string>(Object.entries(seed));
    return {
        getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
        setItem: (k: string, v: string) => void m.set(k, String(v)),
        removeItem: (k: string) => void m.delete(k),
        clear: () => m.clear(),
        key: (i: number) => [...m.keys()][i] ?? null,
        get length() {
            return m.size;
        },
    } as Storage;
}

function jsonResponse(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: {"Content-Type": "application/json"},
    });
}

/** An unsigned JWT carrying the given claims. The client only base64-decodes
 *  the payload for display, so a real signature is never needed here. */
function fakeJwt(claims: Record<string, unknown>): string {
    const b64 = (o: unknown) =>
        btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
    return `${b64({alg: "none"})}.${b64(claims)}.sig`;
}

interface Harness {
    oidc: OidcModule;
    /** Form bodies POSTed to the token endpoint, in order. */
    tokenCalls: URLSearchParams[];
    session: Storage;
    /** Swap to change how the fake token endpoint answers. */
    reply: (form: URLSearchParams) => Response | Promise<Response>;
}

let instance = 0;

async function harness(
    opts: {authEnabled?: boolean; refreshToken?: string | null} = {},
): Promise<Harness> {
    const refresh = opts.refreshToken === undefined ? "refresh-1" : opts.refreshToken;
    const session = fakeStorage(refresh ? {[STORAGE_REFRESH]: refresh} : {});
    const g = globalThis as unknown as Record<string, unknown>;
    g.window = {
        AUTH_ENABLED: opts.authEnabled ?? true,
        AUTH_ISSUER: ISSUER,
        AUTH_CLIENT_ID: "viewer-client",
        location: {origin: "https://app.example.test", pathname: "/", search: "", assign() {}},
    };
    g.sessionStorage = session;
    g.localStorage = fakeStorage();

    const h: Harness = {
        oidc: null as unknown as OidcModule,
        tokenCalls: [],
        session,
        reply: () => jsonResponse(200, {access_token: "scoped-token", expires_in: 3600}),
    };

    g.fetch = async (input: unknown, init?: RequestInit): Promise<Response> => {
        const url = String(input);
        if (url.endsWith("/.well-known/openid-configuration")) {
            return jsonResponse(200, {
                authorization_endpoint: `${ISSUER}/authorize`,
                token_endpoint: TOKEN_ENDPOINT,
                end_session_endpoint: `${ISSUER}/logout`,
            });
        }
        if (url === TOKEN_ENDPOINT) {
            const form = new URLSearchParams(String(init?.body ?? ""));
            h.tokenCalls.push(form);
            return h.reply(form);
        }
        throw new Error(`unexpected fetch: ${url}`);
    };

    // A fresh module instance per test: the client keeps its token state at
    // module scope, so the query string is there to defeat the ESM cache.
    h.oidc = (await import(`../../services/auth/oidc?i=${++instance}`)) as unknown as OidcModule;
    return h;
}

test("redeems the refresh token against the requested scope", async () => {
    const h = await harness();
    const token = await h.oidc.getAccessTokenForScope(OTHER_SCOPE);

    assert.equal(token, "scoped-token");
    assert.equal(h.tokenCalls.length, 1);
    const form = h.tokenCalls[0];
    assert.equal(form.get("grant_type"), "refresh_token");
    assert.equal(form.get("refresh_token"), "refresh-1");
    assert.equal(form.get("client_id"), "viewer-client");
    assert.equal(form.get("scope"), OTHER_SCOPE);
});

test("caches per scope — a different scope is a different token", async () => {
    const h = await harness();
    h.reply = (form) =>
        jsonResponse(200, {access_token: `token-for-${form.get("scope")}`, expires_in: 3600});

    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), `token-for-${OTHER_SCOPE}`);
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), `token-for-${OTHER_SCOPE}`);
    assert.equal(h.tokenCalls.length, 1, "the second call for the same scope must be cached");

    assert.equal(await h.oidc.getAccessTokenForScope("third-api.read"), "token-for-third-api.read");
    assert.equal(h.tokenCalls.length, 2, "a different scope needs its own token");
});

test("single-flights concurrent callers of the same scope", async () => {
    const h = await harness();
    let release: (() => void) | null = null;
    const gate = new Promise<void>((r) => {
        release = r;
    });
    h.reply = async () => {
        await gate;
        return jsonResponse(200, {access_token: "scoped-token", expires_in: 3600});
    };

    const all = Promise.all([
        h.oidc.getAccessTokenForScope(OTHER_SCOPE),
        h.oidc.getAccessTokenForScope(OTHER_SCOPE),
        h.oidc.getAccessTokenForScope(OTHER_SCOPE),
        h.oidc.getAccessTokenForScope(OTHER_SCOPE),
    ]);
    (release as unknown as () => void)();

    assert.deepEqual(await all, Array(4).fill("scoped-token"));
    assert.equal(h.tokenCalls.length, 1);
});

test("re-acquires once the cached token falls inside the expiry skew", async () => {
    const h = await harness();
    // Shorter than the 30s skew, so the cached entry is stale on arrival.
    h.reply = () => jsonResponse(200, {access_token: "short-lived", expires_in: 10});

    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), "short-lived");
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), "short-lived");
    assert.equal(h.tokenCalls.length, 2);
});

test("leaves the primary token, its expiry and the user claims alone", async () => {
    const h = await harness();
    const primary = fakeJwt({sub: "user-1", email: "someone@example.test"});
    h.reply = () =>
        jsonResponse(200, {
            access_token: primary,
            expires_in: 3600,
            id_token: fakeJwt({sub: "user-1", email: "someone@example.test", name: "Some One"}),
        });
    assert.equal(await h.oidc.refreshAccessToken(), true);

    h.reply = () => jsonResponse(200, {access_token: "scoped-token", expires_in: 3600});
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), "scoped-token");

    assert.equal(h.oidc.isSignedIn(), true);
    assert.equal(h.oidc.getAccessToken(), primary, "the primary token must not be overwritten");
    assert.deepEqual(h.oidc.getUser(), {
        sub: "user-1",
        email: "someone@example.test",
        name: "Some One",
    });
});

test("adopts a rotated refresh token so the session survives", async () => {
    const h = await harness();
    h.reply = () =>
        jsonResponse(200, {
            access_token: "scoped-token",
            expires_in: 3600,
            refresh_token: "refresh-2",
        });

    await h.oidc.getAccessTokenForScope(OTHER_SCOPE);
    assert.equal(h.session.getItem(STORAGE_REFRESH), "refresh-2");

    await h.oidc.getAccessTokenForScope("third-api.read");
    assert.equal(h.tokenCalls[1].get("refresh_token"), "refresh-2");
});

test("returns null when auth is disabled for this deployment", async () => {
    const h = await harness({authEnabled: false});
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), null);
    assert.equal(h.tokenCalls.length, 0);
});

test("returns null when there is no session to exchange", async () => {
    const h = await harness({refreshToken: null});
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), null);
    assert.equal(h.tokenCalls.length, 0);
});

test("throws a typed error when the provider refuses the scope", async () => {
    const h = await harness();
    h.reply = () => jsonResponse(400, {error: "invalid_scope"});

    await assert.rejects(
        () => h.oidc.getAccessTokenForScope(OTHER_SCOPE),
        (e: unknown) => {
            assert.ok(e instanceof h.oidc.ScopedTokenError);
            assert.equal(e.status, 400);
            assert.match(e.message, /other-api\.read/);
            return true;
        },
    );
    // A refused scope says nothing about the session — it must survive.
    assert.equal(h.session.getItem(STORAGE_REFRESH), "refresh-1");
});

test("wraps a transport failure rather than leaking the rejection", async () => {
    const h = await harness();
    h.reply = () => {
        throw new TypeError("Failed to fetch");
    };

    await assert.rejects(
        () => h.oidc.getAccessTokenForScope(OTHER_SCOPE),
        (e: unknown) => {
            assert.ok(e instanceof h.oidc.ScopedTokenError);
            assert.equal(e.status, undefined);
            assert.match(e.message, /Failed to fetch/);
            return true;
        },
    );
});

test("rejects a response that carries no access token", async () => {
    const h = await harness();
    h.reply = () => jsonResponse(200, {token_type: "Bearer"});

    await assert.rejects(
        () => h.oidc.getAccessTokenForScope(OTHER_SCOPE),
        (e: unknown) => e instanceof h.oidc.ScopedTokenError,
    );
});

test("rejects an empty scope instead of handing back an empty token", async () => {
    const h = await harness();
    await assert.rejects(
        () => h.oidc.getAccessTokenForScope("   "),
        (e: unknown) => e instanceof h.oidc.ScopedTokenError,
    );
    assert.equal(h.tokenCalls.length, 0);
});

test("sign-out drops the per-resource cache", async () => {
    const h = await harness();
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), "scoped-token");

    await h.oidc.signOut();

    // No session left, so nothing to exchange and nothing cached to hand back.
    assert.equal(await h.oidc.getAccessTokenForScope(OTHER_SCOPE), null);
    assert.equal(h.tokenCalls.length, 1);
});
