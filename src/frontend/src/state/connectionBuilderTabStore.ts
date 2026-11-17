import { create } from "zustand";

/** The connection builder panel's own tab: the builder. A plugin's tab is its panel id. */
export const BUILDER_TAB = "builder";

// The connection builder panel's last picked tab, kept outside the panel so it survives
// closing it and moving it between the column, a group and a window. For the page only.
interface ConnectionBuilderTabState {
  tab: string;
  select: (tab: string) => void;
}

export const useConnectionBuilderTabStore = create<ConnectionBuilderTabState>((set) => ({
  tab: BUILDER_TAB,
  select: (tab) => set({ tab }),
}));
