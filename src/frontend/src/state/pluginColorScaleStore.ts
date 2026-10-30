import {create} from "zustand";

import type {ColorScale} from "@/utils/colorScale";

/** The banded scale a plugin painted with (SceneHandle.showColorScale), which the colour legend
 *  shows instead of the result colormap while it is set. Owned: only its owner clears it. */
interface PluginColorScaleState {
    owner: string | null;
    scale: ColorScale | null;
    show: (owner: string, scale: ColorScale | null) => void;
}

export const usePluginColorScaleStore = create<PluginColorScaleState>((set, get) => ({
    owner: null,
    scale: null,
    show: (owner, scale) => {
        if (scale) set({owner, scale});
        else if (get().owner === owner) set({owner: null, scale: null});
    },
}));
