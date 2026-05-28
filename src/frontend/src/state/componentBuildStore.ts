// Job records for in-flight `component_build` jobs. Mirror of
// conversionStore — different namespace so a parallel CAD conversion
// + component build can both run without trampling each other's UI
// state.
//
// The pipeline (Stage 10, services/components/...) calls setJob on
// every poll tick so the panel can render progress + the final GLB
// URL without prop drilling.

import {create} from "zustand";

import type {ConvertStatus} from "@/services/viewerApi";

export interface ComponentBuildJob {
    specName: string;
    jobId: string;
    derivedKey: string;
    status: ConvertStatus;
    progress: number;
    stage: string;
    error: string | null;
    startedAt: number;
}

type ComponentBuildState = {
    /** Keyed by spec_name so re-submitting the same spec replaces the
     *  prior job's row — the panel only shows the latest build per
     *  spec. */
    jobs: Record<string, ComponentBuildJob>;
    setJob: (specName: string, job: ComponentBuildJob) => void;
    clearJob: (specName: string) => void;
};

export const useComponentBuildStore = create<ComponentBuildState>((set) => ({
    jobs: {},
    setJob: (specName, job) =>
        set((s) => ({jobs: {...s.jobs, [specName]: job}})),
    clearJob: (specName) =>
        set((s) => {
            const next = {...s.jobs};
            delete next[specName];
            return {jobs: next};
        }),
}));
