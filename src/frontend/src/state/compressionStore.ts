import {create} from "zustand";
import {CompressionSweepState} from "@/services/viewerApi";

// Mirrors conversionStore in shape — per-key map of in-flight /
// recently-completed compression sweeps, populated by a polling
// effect (see App.tsx). State lives server-side in NATS KV so a
// reload picks up an in-progress sweep started in another session.

type CompressionState = {
    sweeps: Record<string, CompressionSweepState>;
    setSweeps: (sweeps: Record<string, CompressionSweepState>) => void;
    clearSweep: (scopeLabel: string) => void;
};

export const useCompressionStore = create<CompressionState>((set) => ({
    sweeps: {},
    setSweeps: (sweeps) => set({sweeps}),
    clearSweep: (scopeLabel) =>
        set((s) => {
            const next = {...s.sweeps};
            delete next[scopeLabel];
            return {sweeps: next};
        }),
}));
