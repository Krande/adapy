import {create} from 'zustand';

interface ModelState {
    modelUrl: string | null;
    setModelUrl: (url: string | null) => void;
}

export const useModelStore = create<ModelState>((set) => ({
    modelUrl: null,
    setModelUrl: (url) => set({modelUrl: url}),
}));
