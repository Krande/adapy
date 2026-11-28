// THE resolver: which engine runs an operation, in what order.
//
// Three inputs, one answer:
//   1. the effective execution policy (`GET /api/me` -> `execution_policy`: the
//      administrator's global order, overlaid by the user's admin-set override),
//      which says which engines are ALLOWED for (operation, qualifier) and their
//      default order;
//   2. worker access (`GET /api/me` -> `worker_access`): without it the worker is
//      not allowed, whatever the order says;
//   3. the user's own preference (Settings -> Execution engine), which may only
//      move an allowed engine to the front.
// Engines that cannot handle the input are skipped (`supports`), e.g. the native
// module only covers STEP/IFC -> GLB.
//
// The server enforces the worker part: a submission sent with `X-Ada-Run-On:
// worker` is refused when the policy does not list the worker (execution_policy.py).
// A workload with NO browser path (nothing but the worker can run it) is not a
// choice: it goes to the worker without that header, for everyone, as before.
//
// Kept free of the pyodide worker and of any pipeline: conversion/index.ts imports
// this statically, and those must stay out of the embed bundle.

import type {WorkerAccessPolicy} from "@/services/api/auth";
import {useExecutionPolicyStore, useExecutionPreferenceStore} from "@/state/executionPolicyStore";
import {
    applyPreference,
    defaultExecutionPolicy,
    ENGINE_LABELS,
    lookupOrder,
    WILDCARD,
    type ExecEngine,
    type ExecOperation,
    type ExecutionPolicyDoc,
} from "./executionPolicyModel";
import {canUseWorker, workerAccessSnapshot} from "./workerAccess";

export type {ExecEngine, ExecOperation, ExecutionPolicyDoc} from "./executionPolicyModel";

export interface ResolveOptions {
    /** The effective policy (default: the store, else the defaults). */
    policy?: ExecutionPolicyDoc | null;
    /** Worker access (default: the store). */
    workerAccess?: WorkerAccessPolicy | null;
    /** The worker capability a per-capability grant would name (plugin jobs). Without
     * one only GLOBAL worker access lets browser-capable work move to the worker. */
    capability?: string | null;
    /** The user's preferred engine (default: the preference store; null = none). */
    preference?: ExecEngine | null;
    /** Can this engine handle the input? The worker is always assumed to. */
    supports?: (engine: ExecEngine) => boolean;
}

/** The effective policy now: the one /api/me returned, or the defaults. */
export function effectivePolicy(): ExecutionPolicyDoc {
    return useExecutionPolicyStore.getState().policy ?? defaultExecutionPolicy();
}

function workerAllowed(opts: ResolveOptions): boolean {
    const access = opts.workerAccess !== undefined ? opts.workerAccess : workerAccessSnapshot();
    return canUseWorker(access, opts.capability ?? null);
}

/** The engines the policy allows this user for (op, qualifier), in the policy's
 * order: the admin order minus the worker when the user has no worker access. */
export function allowedEngines(op: ExecOperation, qualifier?: string | null, opts: ResolveOptions = {}): ExecEngine[] {
    const policy = opts.policy ?? effectivePolicy();
    const order = lookupOrder(policy, op, qualifier);
    const worker = workerAllowed(opts);
    return order.filter((e) => e !== "worker" || worker);
}

/** The order to try engines in for (op, qualifier): the allowed engines, the
 * user's preferred one first, minus the ones that cannot handle the input. */
export function engineOrder(op: ExecOperation, qualifier?: string | null, opts: ResolveOptions = {}): ExecEngine[] {
    const preference =
        opts.preference !== undefined ? opts.preference : useExecutionPreferenceStore.getState().preferred[op] ?? null;
    const ordered = applyPreference(allowedEngines(op, qualifier, opts), preference);
    const supports = opts.supports;
    return supports ? ordered.filter((e) => e === "worker" || supports(e)) : ordered;
}

export interface ExecutionPlan {
    op: ExecOperation;
    qualifier: string;
    /** Engines to try, in order. Empty: the policy allows nothing that can run it. */
    engines: ExecEngine[];
    /** Some browser engine can handle the input, so running on the worker is a
     * CHOICE (sent as `X-Ada-Run-On: worker` and judged by the server). False: the
     * worker is the only path and runs it unjudged. */
    browserPath: boolean;
}

/** The plan for one run of (op, qualifier) given what each engine supports. */
export function planExecution(
    op: ExecOperation,
    qualifier: string | null | undefined,
    supports: (engine: ExecEngine) => boolean,
    opts: Omit<ResolveOptions, "supports"> = {},
): ExecutionPlan {
    const q = (qualifier ?? WILDCARD).toLowerCase() || WILDCARD;
    const browserPath = supports("native") || supports("pyodide");
    if (!browserPath) return {op, qualifier: q, engines: ["worker"], browserPath};
    return {op, qualifier: q, engines: engineOrder(op, q, {...opts, supports}), browserPath};
}

/** Does a browser-capable (op, qualifier) go to the worker first? The successor of
 * the old binary "Conversion engine: server worker" switch. */
export function workerPreferred(op: ExecOperation = "convert", qualifier: string = WILDCARD, opts: ResolveOptions = {}): boolean {
    return engineOrder(op, qualifier, opts)[0] === "worker";
}

/** Why a run fell back from one engine to the next. */
export interface FallbackRecord {
    engine: ExecEngine;
    reason: string;
}

export interface AttemptContext {
    engine: ExecEngine;
    /** The worker was chosen over a browser engine: send `X-Ada-Run-On: worker`. */
    explicitWorker: boolean;
    /** The engines tried before this one and why each was passed over. */
    fallbacks: readonly FallbackRecord[];
}

export type EngineRunners<T> = Partial<Record<ExecEngine, (ctx: AttemptContext) => Promise<T>>>;

export interface FallbackResult<T> {
    value: T;
    engine: ExecEngine;
    fallbacks: FallbackRecord[];
}

/** Raised when the policy leaves no engine that can run the operation. */
export class NoAllowedEngineError extends Error {
    constructor(plan: Pick<ExecutionPlan, "op" | "qualifier">, detail?: string) {
        // No concatenated literals: the bundle-provenance check matches bundle
        // strings verbatim against the source, and the minifier folds them.
        const ask = "Ask an administrator to change the execution policy in the Admin Panel (Execution).";
        super(`No execution engine is allowed for ${plan.op} (${plan.qualifier})${detail ? `: ${detail}` : ""}. ${ask}`);
        this.name = "NoAllowedEngineError";
    }
}

function isAbort(err: unknown): boolean {
    return (err as {name?: string} | null)?.name === "AbortError";
}

function reasonOf(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/** Run ``plan``'s engines in order until one succeeds. An engine with no runner is
 * skipped; one that throws hands over to the next (an abort stops the chain); the
 * last failure is rethrown. Every fallback is logged and handed to later attempts
 * (``ctx.fallbacks``) so the engine that ran can record why it is running. */
export async function runWithFallback<T>(
    plan: ExecutionPlan,
    runners: EngineRunners<T>,
    opts: {label?: string; log?: (msg: string) => void} = {},
): Promise<FallbackResult<T>> {
    const log = opts.log ?? ((m: string) => console.info(m));
    const label = opts.label ?? `${plan.op} ${plan.qualifier}`;
    const fallbacks: FallbackRecord[] = [];
    const runnable = plan.engines.filter((e) => runners[e]);
    if (runnable.length === 0) throw new NoAllowedEngineError(plan);
    let lastError: unknown = null;
    for (let i = 0; i < runnable.length; i++) {
        const engine = runnable[i];
        const run = runners[engine]!;
        try {
            const value = await run({
                engine,
                explicitWorker: engine === "worker" && plan.browserPath,
                fallbacks: [...fallbacks],
            });
            if (fallbacks.length) {
                const tried = fallbacks.map((f) => `${ENGINE_LABELS[f.engine]} (${f.reason})`).join(", ");
                log(`[execution] ${label}: ran on ${ENGINE_LABELS[engine]} after ${tried}`);
            }
            return {value, engine, fallbacks};
        } catch (err) {
            if (isAbort(err)) throw err;
            lastError = err;
            const reason = reasonOf(err);
            fallbacks.push({engine, reason});
            if (i + 1 < runnable.length) {
                log(`[execution] ${label}: ${ENGINE_LABELS[engine]} failed (${reason}); trying ${ENGINE_LABELS[runnable[i + 1]]}`);
            }
        }
    }
    throw lastError;
}

/** What an in-browser pipeline records on its audit row (`audit/local`
 * `execution`): which engine ran and what it fell back from. */
export interface ExecutionAuditInfo {
    engine: ExecEngine;
    fallbacks: FallbackRecord[];
}

/** The audit-row shape of an attempt. */
export function executionAudit(ctx: AttemptContext | null | undefined): ExecutionAuditInfo | undefined {
    if (!ctx) return undefined;
    return {engine: ctx.engine, fallbacks: [...ctx.fallbacks]};
}
