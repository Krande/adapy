import {create} from 'zustand';

type ColorState = {
    min: number;
    max: number;
    step: number;
    minColor: string; // Add this line
    maxColor: string; // Add this line
    setMin: (min: number) => void;
    setMax: (max: number) => void;
    setStep: (step: number) => void;
    setMinColor: (minColor: string) => void; // Add this line
    setMaxColor: (maxColor: string) => void; // Add this line
};

export const useColorStore = create<ColorState>((set) => ({
    min: 0,
    max: 100,
    step: 10,
    minColor: 'rgb(255, 0, 0)', // Add this line
    maxColor: 'rgb(0, 255, 0)', // Add this line
    setMin: (min) => set({ min }),
    setMax: (max) => set({ max }),
    setStep: (step) => set({ step }),
    setMinColor: (minColor) => set({ minColor }), // Add this line
    setMaxColor: (maxColor) => set({ maxColor }), // Add this line
}));