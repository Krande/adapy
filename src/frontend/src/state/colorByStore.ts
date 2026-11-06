import {create} from "zustand";

export interface ColorByLegendEntry {
    label: string;
    /** A CSS colour. */
    color: string;
}

export interface ColorByLegendData {
    title: string;
    entries: ColorByLegendEntry[];
}

/**
 * What the scene is coloured by (`utils/scene/colorBy`). `legend` is set for a
 * colouring core's FEA legend does not draw itself (an FEA property is drawn by
 * the colour legend like any result; a source with its own categories sets this).
 */
export interface ColorByState {
    activeId: string | null;
    legend: ColorByLegendData | null;
    /** The result field shown before an FEA property took the colours, to put back on reset. */
    previousField: {fieldName: string; reduction: string} | null;
    set: (p: Partial<Pick<ColorByState, "activeId" | "legend" | "previousField">>) => void;
}

export const useColorByStore = create<ColorByState>((set) => ({
    activeId: null,
    legend: null,
    previousField: null,
    set: (p) => set(p),
}));
