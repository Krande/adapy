// `stopLocalViewer` / `localShutdownAvailable`: the page side of the local
// viewer's shutdown route (`ada/comms/rest/local_shutdown.py`).

import { test } from "node:test";
import assert from "node:assert/strict";

import { localShutdownAvailable, stopLocalViewer } from "@/services/localViewer";

type Call = { url: string; init?: RequestInit };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function recorder(replies: Response[]) {
  const calls: Call[] = [];
  const fetchImpl = async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = replies.shift();
    if (!next) throw new Error(`unexpected request ${url}`);
    return next;
  };
  return { calls, fetchImpl };
}

test("the capability is read from window.ADA_LOCAL_SHUTDOWN, and only `true` counts", () => {
  const w = globalThis as unknown as { window?: Record<string, unknown> };
  const had = w.window;
  try {
    w.window = {};
    assert.equal(localShutdownAvailable(), false);
    w.window = { ADA_LOCAL_SHUTDOWN: "true" };
    assert.equal(localShutdownAvailable(), false);
    w.window = { ADA_LOCAL_SHUTDOWN: true };
    assert.equal(localShutdownAvailable(), true);
  } finally {
    w.window = had;
  }
});

test("the token from /api/config goes in the header the server names", async () => {
  const { calls, fetchImpl } = recorder([
    json(200, { localShutdown: { available: true, token: "tok", header: "X-Ada-Local-Shutdown" } }),
    json(202, { stopping: true }),
  ]);
  assert.deepEqual(await stopLocalViewer(fetchImpl, "/api"), { ok: true });
  assert.equal(calls[0].url, "/api/config");
  assert.equal(calls[1].url, "/api/local/shutdown");
  assert.equal(calls[1].init?.method, "POST");
  assert.deepEqual(calls[1].init?.headers, { "X-Ada-Local-Shutdown": "tok" });
});

test("a viewer that offers no token is not asked to stop", async () => {
  for (const config of [{}, { localShutdown: { available: false } }, { localShutdown: { available: true } }]) {
    const { calls, fetchImpl } = recorder([json(200, config)]);
    const result = await stopLocalViewer(fetchImpl, "/api");
    assert.equal(result.ok, false);
    assert.equal(calls.length, 1, JSON.stringify(config));
  }
});

test("a refusal is reported with the server's reason, and nothing throws", async () => {
  const { fetchImpl } = recorder([
    json(200, { localShutdown: { available: true, token: "tok" } }),
    json(403, { detail: "wrong or missing local shutdown token" }),
  ]);
  assert.deepEqual(await stopLocalViewer(fetchImpl, "/api"), {
    ok: false,
    error: "wrong or missing local shutdown token",
  });
  const failing = async () => {
    throw new Error("connection refused");
  };
  assert.deepEqual(await stopLocalViewer(failing, "/api"), { ok: false, error: "connection refused" });
});
