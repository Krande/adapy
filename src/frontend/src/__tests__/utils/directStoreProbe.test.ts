import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

import {
    UNREACHABLE_RECHECK_MS,
    directStoreReachable,
    resetDirectStoreProbe,
} from "../../utils/scene/directStoreProbe";

const URL_A = "https://store.example/bucket/a.glb?sig=1";

function okFetch(calls: string[], cancelled: {n: number}): typeof fetch {
    return (async (input: RequestInfo | URL) => {
        calls.push(String(input));
        return {
            ok: true,
            status: 200,
            body: {cancel: async () => void (cancelled.n += 1)},
        } as unknown as Response;
    }) as typeof fetch;
}

function failingFetch(calls: string[]): typeof fetch {
    return (async (input: RequestInfo | URL) => {
        calls.push(String(input));
        throw new TypeError("Failed to fetch");
    }) as typeof fetch;
}

beforeEach(() => resetDirectStoreProbe());

test("a reachable store is probed once and the body is not downloaded", async () => {
    const calls: string[] = [];
    const cancelled = {n: 0};
    const fetchImpl = okFetch(calls, cancelled);
    assert.equal(await directStoreReachable(URL_A, {fetchImpl}), true);
    assert.equal(await directStoreReachable("https://store.example/bucket/b.glb", {fetchImpl}), true);
    assert.deepEqual(calls, [URL_A]);
    assert.equal(cancelled.n, 1);
});

test("an HTTP error response still counts as reachable", async () => {
    const fetchImpl = (async () => ({ok: false, status: 403, body: null}) as unknown as Response) as typeof fetch;
    assert.equal(await directStoreReachable(URL_A, {fetchImpl}), true);
});

test("a network error marks the store unreachable until the recheck window passes", async () => {
    const calls: string[] = [];
    let clock = 1_000;
    const now = () => clock;
    assert.equal(await directStoreReachable(URL_A, {fetchImpl: failingFetch(calls), now}), false);

    clock += UNREACHABLE_RECHECK_MS - 1;
    assert.equal(await directStoreReachable(URL_A, {fetchImpl: failingFetch(calls), now}), false);
    assert.equal(calls.length, 1, "cached verdict, no second probe");

    clock += 2;
    const cancelled = {n: 0};
    assert.equal(await directStoreReachable(URL_A, {fetchImpl: okFetch(calls, cancelled), now}), true);
    assert.equal(calls.length, 2, "re-probed after the window");
});

test("a probe that never answers times out as unreachable", async () => {
    const hanging = ((_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        })) as typeof fetch;
    const started = Date.now();
    assert.equal(await directStoreReachable(URL_A, {fetchImpl: hanging, timeoutMs: 20}), false);
    assert.ok(Date.now() - started < 1_000);
});

test("concurrent loads share a single probe", async () => {
    const calls: string[] = [];
    const cancelled = {n: 0};
    const fetchImpl = okFetch(calls, cancelled);
    const results = await Promise.all([
        directStoreReachable(URL_A, {fetchImpl}),
        directStoreReachable(URL_A, {fetchImpl}),
        directStoreReachable(URL_A, {fetchImpl}),
    ]);
    assert.deepEqual(results, [true, true, true]);
    assert.equal(calls.length, 1);
});
