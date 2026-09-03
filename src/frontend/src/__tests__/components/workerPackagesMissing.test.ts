import assert from "node:assert/strict";
import {test} from "node:test";

// The admin UI shows a worker's package manifest in two places, and both used to
// render ANY failure as red error text. A worker that holds no database pool
// never records a manifest, so its endpoint answers 404 permanently -- a normal
// state that read as "the admin API is broken".
//
// What matters is that exactly one status is treated as "nothing recorded", and
// that a genuine failure is still a failure. A test that only checked the 404
// case would pass just as well if the code swallowed everything, so the negative
// cases below are the load-bearing ones.
//
// `viewerApi` touches browser storage at module scope, so the globals it reads
// are stubbed before importing it -- the same arrangement oidcScopedToken.test
// uses. Nothing here makes a request; only the error TYPE is under test.

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

const g = globalThis as unknown as Record<string, unknown>;
g.sessionStorage = fakeStorage();
g.localStorage = fakeStorage();
g.window = {location: {origin: "https://app.example.test", pathname: "/", search: ""}};

const {ApiError} = await import("../../services/viewerApi");
const {isMissingManifest, MISSING_MANIFEST_NOTE} = await import(
    "../../components/admin/workerPackages"
);

test("a 404 means no manifest was ever recorded", () => {
    assert.equal(isMissingManifest(new ApiError("x failed: 404 Not Found", 404)), true);
});

test("any other failing status is still a real error", () => {
    for (const status of [400, 401, 403, 409, 500, 502, 503]) {
        assert.equal(
            isMissingManifest(new ApiError(`x failed: ${status}`, status)),
            false,
            `status ${status} must not be reported as "no manifest"`,
        );
    }
});

test("a transport failure is not mistaken for an empty manifest", () => {
    // fetch() rejects with a plain TypeError when the request never reached the
    // server. There is no status to inspect, and it must not read as "nothing
    // recorded" -- that would hide an unreachable API behind a benign message.
    assert.equal(isMissingManifest(new TypeError("Failed to fetch")), false);
    assert.equal(isMissingManifest(new Error("boom")), false);
});

test("a non-Error rejection is handled rather than assumed", () => {
    for (const thrown of [undefined, null, "404", 404, {status: 404}]) {
        assert.equal(
            isMissingManifest(thrown),
            false,
            `${JSON.stringify(thrown) ?? "undefined"} is not an ApiError and must not pass`,
        );
    }
});

test("the empty-state note does not read as a failure", () => {
    // It replaces red error text, so it must not carry the vocabulary that sends
    // someone hunting for a fault that does not exist.
    assert.ok(MISSING_MANIFEST_NOTE.length > 0);
    for (const word of ["fail", "error", "404", "not found"]) {
        assert.ok(
            !MISSING_MANIFEST_NOTE.toLowerCase().includes(word),
            `the note should not say "${word}"`,
        );
    }
});
