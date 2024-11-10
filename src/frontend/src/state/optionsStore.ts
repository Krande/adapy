import {create, StoreApi} from 'zustand';

type State = {
    isOptionsVisible: boolean;
    setIsOptionsVisible: (value: boolean) => void;
    showPerf: boolean;
    setShowPerf: (value: boolean) => void;
    showEdges: boolean;
    setShowEdges: (value: boolean) => void;
    lockTranslation: boolean;
    setLockTranslation: (value: boolean) => void;
};

export const useOptionsStore = create<State>((set: StoreApi<State>['setState']) => ({
    isOptionsVisible: false,
    setIsOptionsVisible: (value: boolean) => set(() => ({isOptionsVisible: value})),
    lockTranslation: false,
    setLockTranslation: (value: boolean) => set(() => ({lockTranslation: value})),
    showPerf: false,
    setShowPerf: (value: boolean) => set(() => ({showPerf: value})),
    showEdges: true,
    setShowEdges: (value: boolean) => set(() => ({showEdges: value})),
}));