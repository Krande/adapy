import assert from "node:assert/strict";
import {test} from "node:test";

// The OIDC `prompt` value decides whether the IdP silently re-uses the current
// session, re-asks for credentials, or offers its account chooser. "Switch
// user" needs the chooser: it lets a second account be selected without
// signing the first one out.

type OidcModule = typeof import("../../services/auth/oidc");

const ISSUER = "https://idp.example.test";
let instance = 0;

function fakeStorage(): Storage {
    const m = new Map<string, string>();
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

/** Load a fresh module instance with the browser globals it touches stubbed,
 *  capturing the URL it would navigate to. */
async function harness(): Promise<{oidc: OidcModule; assigned: () => URL | null}> {
    let target: string | null = null;
    const g = globalThis as unknown as Record<string, unknown>;
    g.window = {
        AUTH_ENABLED: true,
        AUTH_ISSUER: ISSUER,
        AUTH_CLIENT_ID: "viewer-client",
        location: {
            origin: "https://app.example.test",
            pathname: "/",
            search: "",
            assign: (u: string) => void (target = u),
        },
    };
    g.sessionStorage = fakeStorage();
    g.localStorage = fakeStorage();
    g.fetch = async (input: unknown): Promise<Response> => {
        if (String(input).endsWith("/.well-known/openid-configuration")) {
            return new Response(
                JSON.stringify({
                    authorization_endpoint: `${ISSUER}/authorize`,
                    token_endpoint: `${ISSUER}/oauth/token`,
                }),
                {status: 200, headers: {"Content-Type": "application/json"}},
            );
        }
        throw new Error(`unexpected fetch: ${String(input)}`);
    };
    // Query string defeats the ESM cache: the client keeps state at module scope.
    const oidc = (await import(`../../services/auth/oidc?i=${++instance}`)) as unknown as OidcModule;
    return {oidc, assigned: () => (target ? new URL(target) : null)};
}

test("no prompt by default, so silent SSO still works", async () => {
    // Sending a prompt on every authorize would defeat SSO entirely.
    const {oidc} = await harness();
    assert.equal(oidc.promptParam(undefined), null);
    assert.equal(oidc.promptParam({}), null);
});

test("switching user asks the IdP for its account chooser", async () => {
    // select_account lets an already-signed-in account be picked without
    // re-entering credentials — the point of switching rather than signing out.
    const {oidc} = await harness();
    assert.equal(oidc.promptParam({selectAccount: true}), "select_account");
});

test("forcing login re-authenticates instead", async () => {
    const {oidc} = await harness();
    assert.equal(oidc.promptParam({forceLogin: true}), "login");
});

test("the account chooser wins over re-authentication", async () => {
    // The more specific request; a password prompt is a worse fit for switching.
    const {oidc} = await harness();
    assert.equal(oidc.promptParam({forceLogin: true, selectAccount: true}), "select_account");
});

test("signIn puts the chooser on the authorize URL", async () => {
    // The contract that actually matters: the helper's value has to reach the IdP.
    const {oidc, assigned} = await harness();
    await oidc.signIn(undefined, {selectAccount: true});
    assert.equal(assigned()?.searchParams.get("prompt"), "select_account");
});

test("an ordinary signIn sends no prompt at all", async () => {
    const {oidc, assigned} = await harness();
    await oidc.signIn();
    assert.equal(assigned()?.searchParams.has("prompt"), false);
});
