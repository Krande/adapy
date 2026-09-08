import {create} from "zustand";
import {persist} from "zustand/middleware";

// What a server-side calculation may spend — cores and memory — as the user
// sets it in the viewer's Settings.
//
// A plugin job (a code check, a bake) runs on a worker whose machine the
// browser knows nothing about. Two things follow. First, these are a REQUEST:
// the worker's own environment caps them, and a plugin echoes the effective
// values back in its job summary. Second, the defaults are "no opinion": a
// user who never opens Settings gets whatever the worker decides, which on a
// workstation is most of the machine and on a sized pod is the pod.
//
// Persisted to localStorage, like the render-performance toggles: a choice
// made for one model holds for the next.

/** How much of the machine to take. Names the trade-off, not a number. */
export type ComputeMode = "interactive" | "balanced" | "throughput";

export const COMPUTE_MODES: readonly ComputeMode[] = ["interactive", "balanced", "throughput"];

export interface ComputeState {
    /** Upper bound on worker processes; null lets the mode decide. */
    maxWorkers: number | null;
    /** Peak resident memory the whole calculation may reach, in GB; null = no limit. */
    memoryLimitGb: number | null;
    mode: ComputeMode;

    setMaxWorkers: (n: number | null) => void;
    setMemoryLimitGb: (gb: number | null) => void;
    setMode: (mode: ComputeMode) => void;
    /** Back to "no opinion" on every field. */
    reset: () => void;
}

/** A worker count as the store keeps it: a whole number of at least one, or null. */
export function normaliseWorkers(n: number | null | undefined): number | null {
    if (n === null || n === undefined || !Number.isFinite(n)) return null;
    return Math.max(1, Math.round(n));
}

/** A memory limit as the store keeps it: a positive number of GB, or null. */
export function normaliseMemoryGb(gb: number | null | undefined): number | null {
    if (gb === null || gb === undefined || !Number.isFinite(gb) || gb <= 0) return null;
    return Math.round(gb * 10) / 10;
}

export const isComputeMode = (v: unknown): v is ComputeMode =>
    typeof v === "string" && (COMPUTE_MODES as readonly string[]).includes(v);

const DEFAULTS = {maxWorkers: null, memoryLimitGb: null, mode: "balanced" as ComputeMode};

export const useComputeStore = create<ComputeState>()(
    persist(
        (set) => ({
            ...DEFAULTS,
            setMaxWorkers: (n) => set({maxWorkers: normaliseWorkers(n)}),
            setMemoryLimitGb: (gb) => set({memoryLimitGb: normaliseMemoryGb(gb)}),
            setMode: (mode) => set({mode: isComputeMode(mode) ? mode : "balanced"}),
            reset: () => set({...DEFAULTS}),
        }),
        {
            name: "ada-compute",
            partialize: (s) => ({maxWorkers: s.maxWorkers, memoryLimitGb: s.memoryLimitGb, mode: s.mode}),
            merge: (persisted, current) => {
                const p = (persisted ?? {}) as Partial<ComputeState>;
                return {
                    ...current,
                    maxWorkers: normaliseWorkers(p.maxWorkers),
                    memoryLimitGb: normaliseMemoryGb(p.memoryLimitGb),
                    mode: isComputeMode(p.mode) ? p.mode : "balanced",
                };
            },
        },
    ),
);

/** The option keys a backend plugin job takes, from the given state.
 *
 *  One shape for every job so a plugin never invents its own spelling: `workers`
 *  and `memory_limit_gb` are null when the user has no opinion, and the mode is
 *  always sent. Pure, so it is testable without the store. */
export function computeJobOptionsFrom(
    s: Pick<ComputeState, "maxWorkers" | "memoryLimitGb" | "mode">,
): {workers: number | null; memory_limit_gb: number | null; performance_mode: ComputeMode} {
    return {
        workers: normaliseWorkers(s.maxWorkers),
        memory_limit_gb: normaliseMemoryGb(s.memoryLimitGb),
        performance_mode: isComputeMode(s.mode) ? s.mode : "balanced",
    };
}

/** The current Settings, as job options. */
export const computeJobOptions = () => computeJobOptionsFrom(useComputeStore.getState());
