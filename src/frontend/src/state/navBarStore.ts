import {create, StoreApi} from 'zustand';

type State = {
    isNavBarVisible: boolean;
    setIsNavBarVisible: (value: boolean) => void;
    showPerf: boolean;
    setShowPerf: (value: boolean) => void;
};

export const useNavBarStore = create<State>((set: StoreApi<State>['setState']) => ({
    isNavBarVisible: false,
    setIsNavBarVisible: (value: boolean) => set(() => ({isNavBarVisible: value})),
    showPerf: false,
    setShowPerf: (value: boolean) => set(() => ({showPerf: value})),
}));