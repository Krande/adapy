import {create} from "zustand";
import {persist} from "zustand/middleware";

import type {ExecEngine, ExecOperation, ExecutionPolicyDoc} from "@/services/compute/executionPolicyModel";
import {EXEC_OPERATIONS} from "@/services/compute/executionPolicyModel";

// The caller's EFFECTIVE execution policy (`GET /api/me` -> `execution_policy`):
// the administrator's global order overlaid by the user's admin-set override.
// null until /api/me answered; the resolver uses the defaults meanwhile (and on a
// deployment without an API, e.g. the embed).
interface ExecutionPolicyState {
    policy: ExecutionPolicyDoc | null;
    setPolicy: (policy: ExecutionPolicyDoc | null) => void;
}

export const useExecutionPolicyStore = create<ExecutionPolicyState>()((set) => ({
    policy: null,
    setPolicy: (policy) => set({policy}),
}));

// The user's own choice, WITHIN the policy: per operation, which of the engines
// the policy allows them should run first. Absent = as configured. Replaces the
// binary "Conversion engine" setting (localStorage "ada-conversion-engine"): a
// user who had picked the server worker there starts with the worker preferred
// for every operation, which is what that setting meant.
export type ExecutionPreferences = Partial<Record<ExecOperation, ExecEngine>>;

interface ExecutionPreferenceState {
    preferred: ExecutionPreferences;
    setPreferred: (op: ExecOperation, engine: ExecEngine | null) => void;
    /** One engine first for every operation (null: as configured everywhere). */
    setPreferredAll: (engine: ExecEngine | null) => void;
}

const LEGACY_ENGINE_KEY = "ada-conversion-engine";

/** The preferences the old binary setting implies (exported for its test). */
export function legacyPreferences(): ExecutionPreferences {
    try {
        const raw = globalThis.localStorage?.getItem(LEGACY_ENGINE_KEY);
        if (!raw) return {};
        const engine = (JSON.parse(raw) as {state?: {engine?: string}})?.state?.engine;
        if (engine !== "worker") return {};
        return Object.fromEntries(EXEC_OPERATIONS.map((op) => [op, "worker"])) as ExecutionPreferences;
    } catch {
        return {};
    }
}

export const useExecutionPreferenceStore = create<ExecutionPreferenceState>()(
    persist(
        (set) => ({
            preferred: legacyPreferences(),
            setPreferred: (op, engine) =>
                set((s) => {
                    const next = {...s.preferred};
                    if (engine) next[op] = engine;
                    else delete next[op];
                    return {preferred: next};
                }),
            setPreferredAll: (engine) =>
                set({
                    preferred: engine
                        ? (Object.fromEntries(EXEC_OPERATIONS.map((op) => [op, engine])) as ExecutionPreferences)
                        : {},
                }),
        }),
        {name: "ada-execution-preference"},
    ),
);
