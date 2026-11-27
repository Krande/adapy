// The caller's effective worker-pool policy (`GET /api/me` → `worker_access`),
// held in a store so routing decisions can read it synchronously and the
// settings panel re-renders when it arrives.
//
// Worker access is opt-in: until an administrator grants it (globally, or per
// capability) every user runs in the browser, and the server rejects their
// worker jobs with 403 regardless of what this client believes.

import {create} from "zustand";

import {authApi, type WorkerAccessPolicy} from "@/services/api/auth";
import {useExecutionPolicyStore} from "@/state/executionPolicyStore";

// Kept free of the pyodide worker: conversion/index.ts imports this module
// statically, and pyodide_converter must stay out of the embed bundle.

/** Check whether the caller's `worker_access` policy (from `GET /api/me`) allows
 * enqueuing a server worker job for `capability`. Mirrors backend
 * `ada.comms.rest.worker_access.is_worker_allowed`. */
export function canUseWorker(
    policy: WorkerAccessPolicy | null | undefined,
    capability?: string | null,
): boolean {
    if (!policy) return false;
    if (policy.effective_all || policy.enabled) return true;
    if (!capability) return false;
    const target = capability.trim().toLowerCase();
    if (!target) return false;
    return (policy.allowed_capabilities ?? []).some((c) => c.trim().toLowerCase() === target);
}

interface WorkerAccessState {
    /** null until `/api/me` answered (or when it failed: treated as no access). */
    policy: WorkerAccessPolicy | null;
    loaded: boolean;
}

export const useWorkerAccessStore = create<WorkerAccessState>()(() => ({
    policy: null,
    loaded: false,
}));

let inflight: Promise<WorkerAccessPolicy | null> | null = null;

/** Fetch (once) and cache the caller's worker policy. `force` re-reads it, e.g.
 * after an administrator changed it. */
export function loadWorkerAccess(force = false): Promise<WorkerAccessPolicy | null> {
    if (!inflight || force) {
        inflight = authApi
            .me()
            .then((me) => {
                // The execution policy rides on the same answer (executionPolicy.ts).
                useExecutionPolicyStore.getState().setPolicy(me.execution_policy ?? null);
                return me.worker_access ?? null;
            })
            .catch(() => null)
            .then((policy) => {
                useWorkerAccessStore.setState({policy, loaded: true});
                return policy;
            });
    }
    return inflight;
}

/** The policy if known, without waiting. */
export function workerAccessSnapshot(): WorkerAccessPolicy | null {
    return useWorkerAccessStore.getState().policy;
}

/** True when the user has GLOBAL worker access — the only grant that lets them
 * move browser-capable work (conversions, bakes) to the worker. A
 * per-capability grant is for the server-only workload it names. */
export function hasGlobalWorkerAccess(policy: WorkerAccessPolicy | null = workerAccessSnapshot()): boolean {
    return canUseWorker(policy, null);
}

/** True when the user has any worker access at all (global or a capability). */
export function hasAnyWorkerAccess(policy: WorkerAccessPolicy | null = workerAccessSnapshot()): boolean {
    return hasGlobalWorkerAccess(policy) || (policy?.allowed_capabilities?.length ?? 0) > 0;
}

/** The notice shown when a workload has no browser path and the user has no
 * worker access. `what` names the workload, e.g. "Opening .odb files". */
export function serverOnlyNotice(what: string, capability?: string): string {
    const grant = capability ? `worker pool access ('${capability}')` : "worker pool access";
    return (
        `${what} runs on the server worker pool, and your account has no ${grant}. ` +
        "Ask an administrator to enable it in the Admin Panel (Users)."
    );
}
