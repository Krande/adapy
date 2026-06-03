import {create} from 'zustand';

export type ConvertStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled';

export interface ConversionJob {
    sourceKey: string;
    jobId: string;
    derivedKey: string;
    status: ConvertStatus;
    progress: number;
    stage: string;
    error: string | null;
    startedAt: number;
}

type ConversionState = {
    jobs: Record<string, ConversionJob>;
    setJob: (sourceKey: string, job: ConversionJob) => void;
    clearJob: (sourceKey: string) => void;
};

export const useConversionStore = create<ConversionState>((set) => ({
    jobs: {},
    setJob: (sourceKey, job) =>
        set((s) => ({jobs: {...s.jobs, [sourceKey]: job}})),
    clearJob: (sourceKey) =>
        set((s) => {
            const next = {...s.jobs};
            delete next[sourceKey];
            return {jobs: next};
        }),
}));
