// One "poll until terminal" loop, parameterised — the shape every inline
// `await new Promise((r) => setTimeout(r, ms))` loop in the viewer follows.
//
// The loops differ on purpose, not by accident: cadence (1.5 s job status vs
// 100 ms scene wait), what counts as terminal (`done|error` vs `done|error|
// cancelled` vs "the scene ref is set"), whether the first read happens before
// or after the first sleep, what to do with a failed read (log and retry,
// stop, throw), and what to do when the ceiling is hit (return quietly, throw
// a timeout). So the loop takes every one of those as an option instead of
// picking a policy — a caller expresses its existing behaviour exactly, and a
// second copy of the loop is never needed.
//
// `pollUntilTerminal` is the plain async function; `useJobPoll` is the React
// wrapper that starts it in an effect, exposes the latest status, and aborts
// on unmount.

import {useEffect, useRef, useState} from "react";

/** A flag the caller can flip to stop a poll — a scope change, an unmount. An
 * `AbortSignal` works too. */
export type PollSignal = {readonly aborted: boolean};

export type PollErrorPolicy = "continue" | "stop" | "throw";

export interface PollOptions<S> {
    /** Read the current status. */
    fetch: () => Promise<S>;
    /** Terminal detection: a list of status strings (matched against
     * `statusOf(s)`) or a predicate. */
    terminal: readonly string[] | ((s: S) => boolean);
    /** How to read the status string when `terminal` is a list. Defaults to
     * `s.status`. */
    statusOf?: (s: S) => string | null | undefined;
    /** Sleep between reads. */
    intervalMs: number;
    /** Stop after this many reads (jobTracking's 45-minute ceiling). */
    maxAttempts?: number;
    /** Give up this many ms after the poll starts. */
    deadlineMs?: number;
    /** Read once before the first sleep (an enqueue that returns no status
     * needs an immediate read; a cache hit is then free). Default: sleep
     * first. */
    fetchFirst?: boolean;
    /** Aborts the loop between reads. */
    signal?: PollSignal | AbortSignal;
    /** Checked before every read; returning false ends the poll as "stopped"
     * (a dismissed toast, an unmounted panel). Runs before the request, not
     * after it, so an unreachable server cannot keep a dismissed poll alive. */
    shouldContinue?: () => boolean;
    /** Called with every successful read, terminal or not. Return "stop" to
     * end the poll early (the entry vanished while the request was in flight). */
    onTick?: (s: S, attempt: number) => void | "stop";
    /** What a failed read means. "continue" (default) keeps polling — a blip
     * must not poison a toast; "throw" propagates; a function decides per
     * error (a 404 is not a blip: the job is gone). */
    onFetchError?: PollErrorPolicy | ((err: unknown, attempt: number) => PollErrorPolicy);
    /** Hitting `maxAttempts` / `deadlineMs`: return `{outcome: "timeout"}`
     * (default) or throw a `PollTimeoutError`. */
    onTimeout?: "return" | "throw";
    /** Message for the thrown timeout. */
    timeoutMessage?: string | (() => string);
    /** An abort: return `{outcome: "aborted"}` (default) or throw a
     * `PollAbortedError`. */
    onAbort?: "return" | "throw";
    abortMessage?: string;
    /** Injectable sleep (tests use fake timers). */
    sleep?: (ms: number) => Promise<void>;
    /** Injectable clock for the deadline. */
    now?: () => number;
}

export type PollOutcome = "terminal" | "timeout" | "aborted" | "stopped";

export interface PollResult<S> {
    outcome: PollOutcome;
    /** The last status read, or null when nothing was read. */
    status: S | null;
    /** Number of reads made. */
    attempts: number;
}

export class PollTimeoutError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "PollTimeoutError";
    }
}

export class PollAbortedError extends Error {
    constructor(message = "aborted") {
        super(message);
        this.name = "PollAbortedError";
    }
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function isTerminalWith<S>(opts: PollOptions<S>, s: S): boolean {
    if (typeof opts.terminal === "function") return opts.terminal(s);
    const statusOf = opts.statusOf ?? ((v: S) => (v as {status?: string | null}).status);
    const st = statusOf(s);
    return st != null && opts.terminal.includes(st);
}

/** Poll `fetch` until a terminal status, a ceiling, an abort, or a stop.
 *
 * Never throws unless an option says so (`onFetchError: "throw"`,
 * `onTimeout: "throw"`, `onAbort: "throw"`). */
export async function pollUntilTerminal<S>(opts: PollOptions<S>): Promise<PollResult<S>> {
    const sleep = opts.sleep ?? defaultSleep;
    const now = opts.now ?? Date.now;
    const startedAt = now();
    const deadline = opts.deadlineMs != null ? startedAt + opts.deadlineMs : null;
    const maxAttempts = opts.maxAttempts ?? Infinity;
    let attempts = 0;
    let last: S | null = null;

    const aborted = () => !!opts.signal?.aborted;
    const abortResult = (): PollResult<S> => {
        if (opts.onAbort === "throw") throw new PollAbortedError(opts.abortMessage);
        return {outcome: "aborted", status: last, attempts};
    };
    const timeoutResult = (): PollResult<S> => {
        if (opts.onTimeout === "throw") {
            const msg = typeof opts.timeoutMessage === "function"
                ? opts.timeoutMessage()
                : opts.timeoutMessage ?? `poll timed out after ${attempts} attempt(s)`;
            throw new PollTimeoutError(msg);
        }
        return {outcome: "timeout", status: last, attempts};
    };

    // One read; returns a result to finish with, or null to keep going.
    const readOnce = async (): Promise<PollResult<S> | null> => {
        if (opts.shouldContinue && !opts.shouldContinue()) {
            return {outcome: "stopped", status: last, attempts};
        }
        attempts += 1;
        let s: S;
        try {
            s = await opts.fetch();
        } catch (err) {
            const policy = typeof opts.onFetchError === "function"
                ? opts.onFetchError(err, attempts)
                : opts.onFetchError ?? "continue";
            if (policy === "throw") throw err;
            if (policy === "stop") return {outcome: "stopped", status: last, attempts};
            return null;
        }
        last = s;
        if (opts.onTick?.(s, attempts) === "stop") {
            return {outcome: "stopped", status: s, attempts};
        }
        if (isTerminalWith(opts, s)) return {outcome: "terminal", status: s, attempts};
        return null;
    };

    if (opts.fetchFirst) {
        const r = await readOnce();
        if (r) return r;
    }
    for (;;) {
        if (aborted()) return abortResult();
        if (attempts >= maxAttempts) return timeoutResult();
        if (deadline != null && now() > deadline) return timeoutResult();
        await sleep(opts.intervalMs);
        if (aborted()) return abortResult();
        const r = await readOnce();
        if (r) return r;
    }
}

export interface UseJobPollOptions<S> extends Omit<PollOptions<S>, "signal"> {
    /** The poll runs while true; flipping it false aborts. Default true. */
    enabled?: boolean;
    /** Restarts the poll when this changes (a new job id). */
    resetKey?: unknown;
    /** Called once with the final result (not on abort). */
    onDone?: (r: PollResult<S>) => void;
    /** Called with an error thrown by the loop (per the throw policies). */
    onError?: (err: unknown) => void;
}

export interface UseJobPollState<S> {
    /** Latest status read. */
    status: S | null;
    /** True while the loop is running. */
    polling: boolean;
    /** How the last run ended, once it has. */
    outcome: PollOutcome | null;
    error: unknown;
}

/** Run `pollUntilTerminal` from a component; aborts on unmount / disable. */
export function useJobPoll<S>(opts: UseJobPollOptions<S>): UseJobPollState<S> {
    const enabled = opts.enabled ?? true;
    const [state, setState] = useState<UseJobPollState<S>>({
        status: null,
        polling: false,
        outcome: null,
        error: null,
    });
    // The options object is rebuilt every render; the effect keys on the
    // things that mean "a different poll" and reads the rest through a ref.
    const optsRef = useRef(opts);
    optsRef.current = opts;

    useEffect(() => {
        if (!enabled) return;
        const signal = {aborted: false};
        setState({status: null, polling: true, outcome: null, error: null});
        const o = optsRef.current;
        void pollUntilTerminal<S>({
            ...o,
            signal,
            onTick: (s, attempt) => {
                if (!signal.aborted) setState((p) => ({...p, status: s}));
                return optsRef.current.onTick?.(s, attempt);
            },
        })
            .then((r) => {
                if (signal.aborted) return;
                setState({status: r.status, polling: false, outcome: r.outcome, error: null});
                optsRef.current.onDone?.(r);
            })
            .catch((err) => {
                if (signal.aborted) return;
                setState((p) => ({...p, polling: false, error: err}));
                optsRef.current.onError?.(err);
            });
        return () => {
            signal.aborted = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, opts.resetKey]);

    return state;
}
