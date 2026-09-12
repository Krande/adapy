import assert from "node:assert/strict";
import {afterEach, beforeEach, mock, test} from "node:test";

import {PollAbortedError, PollTimeoutError, pollUntilTerminal} from "@/hooks/useJobPoll";

// The shared poll-until-terminal loop, under fake timers. Each case is one of
// the behaviours an existing inline loop relies on: terminal detection,
// sleep-first vs read-first, the attempt and deadline ceilings, the abort
// signal, the per-error policy, and the throw policies.

type Status = {status: string; n: number};

beforeEach(() => {
  mock.timers.enable({apis: ["setTimeout", "Date"]});
});
afterEach(() => {
  mock.timers.reset();
});

/** Let the loop's pending microtasks run, then advance the fake clock. */
async function tick(ms: number): Promise<void> {
  await new Promise<void>((r) => setImmediate(r));
  mock.timers.tick(ms);
  await new Promise<void>((r) => setImmediate(r));
}

function sequence(statuses: string[]): {fetch: () => Promise<Status>; calls: () => number} {
  let i = 0;
  return {
    fetch: async () => {
      const s = statuses[Math.min(i, statuses.length - 1)];
      i += 1;
      return {status: s, n: i};
    },
    calls: () => i,
  };
}

test("reaches a terminal status after sleeping between reads (sleep-first)", async () => {
  const src = sequence(["queued", "running", "done"]);
  const p = pollUntilTerminal<Status>({
    fetch: src.fetch,
    terminal: ["done", "error"],
    intervalMs: 1500,
  });
  await tick(1500);
  await tick(1500);
  await tick(1500);
  const r = await p;
  assert.equal(r.outcome, "terminal");
  assert.equal(r.status?.status, "done");
  assert.equal(r.attempts, 3);
  assert.equal(src.calls(), 3);
});

test("fetchFirst reads before the first sleep, so a cache hit costs no wait", async () => {
  const src = sequence(["done"]);
  const r = await pollUntilTerminal<Status>({
    fetch: src.fetch,
    terminal: ["done"],
    intervalMs: 1500,
    fetchFirst: true,
  });
  assert.equal(r.outcome, "terminal");
  assert.equal(r.attempts, 1);
});

test("a predicate terminal works for non-status values", async () => {
  let scene: object | null = null;
  const p = pollUntilTerminal<object | null>({
    fetch: async () => scene,
    terminal: (s) => s != null,
    fetchFirst: true,
    intervalMs: 100,
    deadlineMs: 15000,
  });
  await tick(100);
  scene = {};
  await tick(100);
  const r = await p;
  assert.equal(r.outcome, "terminal");
});

test("deadline: returns a timeout outcome by default, throws when asked", async () => {
  const src = sequence(["queued"]);
  const quiet = pollUntilTerminal<Status>({
    fetch: src.fetch,
    terminal: ["done"],
    intervalMs: 1000,
    deadlineMs: 2500,
  });
  await tick(1000);
  await tick(1000);
  await tick(1000);
  const r = await quiet;
  assert.equal(r.outcome, "timeout");
  assert.equal(r.status?.status, "queued");

  const src2 = sequence(["queued"]);
  const loud = pollUntilTerminal<Status>({
    fetch: src2.fetch,
    terminal: ["done"],
    intervalMs: 1000,
    deadlineMs: 2500,
    onTimeout: "throw",
    timeoutMessage: "external-models timed out after 2.5s",
  });
  loud.catch(() => undefined); // observed below; avoid an unhandled-rejection race
  await tick(1000);
  await tick(1000);
  await tick(1000);
  await assert.rejects(loud, (e: unknown) => e instanceof PollTimeoutError && /2\.5s/.test((e as Error).message));
});

test("maxAttempts caps the number of reads", async () => {
  const src = sequence(["running"]);
  const p = pollUntilTerminal<Status>({
    fetch: src.fetch,
    terminal: ["done"],
    intervalMs: 10,
    maxAttempts: 3,
    onTimeout: "throw",
    timeoutMessage: () => "component build did not complete within the poll window",
  });
  p.catch(() => undefined);
  for (let i = 0; i < 4; i++) await tick(10);
  await assert.rejects(p, /poll window/);
  assert.equal(src.calls(), 3);
});

test("abort signal stops the loop between reads; onAbort=throw raises PollAbortedError", async () => {
  const src = sequence(["running"]);
  const signal = {aborted: false};
  const p = pollUntilTerminal<Status>({fetch: src.fetch, terminal: ["done"], intervalMs: 100, signal});
  await tick(100);
  signal.aborted = true;
  await tick(100);
  const r = await p;
  assert.equal(r.outcome, "aborted");

  const src2 = sequence(["running"]);
  const signal2 = {aborted: true};
  await assert.rejects(
    pollUntilTerminal<Status>({fetch: src2.fetch, terminal: ["done"], intervalMs: 100, signal: signal2, onAbort: "throw"}),
    (e: unknown) => e instanceof PollAbortedError,
  );
  assert.equal(src2.calls(), 0);
});

test("shouldContinue is checked before the request, so a dismissed poll never reads again", async () => {
  let alive = true;
  const src = sequence(["running"]);
  const p = pollUntilTerminal<Status>({
    fetch: src.fetch,
    terminal: ["done"],
    intervalMs: 100,
    shouldContinue: () => alive,
  });
  await tick(100);
  alive = false;
  await tick(100);
  const r = await p;
  assert.equal(r.outcome, "stopped");
  assert.equal(src.calls(), 1);
});

test("onFetchError: continue keeps polling, stop ends it, throw propagates, a function decides per error", async () => {
  // continue (the default): a blip does not end the poll.
  let n = 0;
  const flaky = async (): Promise<Status> => {
    n += 1;
    if (n === 1) throw new Error("blip");
    return {status: "done", n};
  };
  const p = pollUntilTerminal<Status>({fetch: flaky, terminal: ["done"], intervalMs: 10});
  await tick(10);
  await tick(10);
  const r = await p;
  assert.equal(r.outcome, "terminal");
  assert.equal(r.attempts, 2);

  // throw: the error surfaces.
  const boom = pollUntilTerminal<Status>({
    fetch: async () => { throw new Error("gone"); },
    terminal: ["done"],
    intervalMs: 10,
    onFetchError: "throw",
  });
  boom.catch(() => undefined);
  await tick(10);
  await assert.rejects(boom, /gone/);

  // per-error: a 404 is not a blip.
  const notFound = Object.assign(new Error("not found"), {status: 404});
  const decided = pollUntilTerminal<Status>({
    fetch: async () => { throw notFound; },
    terminal: ["done"],
    intervalMs: 10,
    onFetchError: (err) => ((err as {status?: number}).status === 404 ? "stop" : "continue"),
  });
  await tick(10);
  const d = await decided;
  assert.equal(d.outcome, "stopped");
  assert.equal(d.attempts, 1);
});

test("onTick sees every read and can stop the poll", async () => {
  const src = sequence(["running", "running", "done"]);
  const seen: string[] = [];
  const p = pollUntilTerminal<Status>({
    fetch: src.fetch,
    terminal: ["done"],
    intervalMs: 10,
    onTick: (s) => {
      seen.push(s.status);
      return s.n === 2 ? "stop" : undefined;
    },
  });
  await tick(10);
  await tick(10);
  const r = await p;
  assert.equal(r.outcome, "stopped");
  assert.deepEqual(seen, ["running", "running"]);
});
