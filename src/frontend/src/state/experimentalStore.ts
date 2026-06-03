import {create} from "zustand";
import {persist} from "zustand/middleware";

interface ExperimentalState {
    pyodideConverter: boolean;
    setPyodideConverter: (v: boolean) => void;
}

// Persisted in localStorage — toggle survives reloads but not other
// browsers / incognito sessions, which is exactly what we want for an
// opt-in experimental feature.
export const useExperimentalStore = create<ExperimentalState>()(
    persist(
        (set) => ({
            pyodideConverter: false,
            setPyodideConverter: (v) => set({pyodideConverter: v}),
        }),
        {name: "ada-experimental"},
    ),
);
