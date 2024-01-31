import { create } from 'zustand';

type ObjectInfoState = {
    name: string | null;
    setName: (name: string | null) => void;
    faceIndex: number | null;
    setFaceIndex: (faceIndex: number | null) => void;
    show: boolean;
    toggle: () => void;
};

export const useObjectInfoStore = create<ObjectInfoState>((set) => ({
    name: null,
    setName: (name) => set(() => ({ name })),
    faceIndex: null,
    setFaceIndex: (faceIndex) => set(() => ({ faceIndex })),
    show: false,
    toggle: () => set((state) => ({ show: !state.show })),
}));