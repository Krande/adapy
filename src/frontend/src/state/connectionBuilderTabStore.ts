import { create } from "zustand";

/** The Connections panel's own tab: the builder. A plugin's tab is its panel id (`plugin:panel`). */
export const BUILDER_TAB = "builder";
/** Core's other tabs there, which were the Scene panel's Joints group: the clash check, what the
 *  joint tabs draw in 3D, and the review of the joints found or detailed. */
export const CLASHES_TAB = "clashes";
export const JOINT_OVERLAYS_TAB = "jointOverlays";
export const JOINTS_REVIEW_TAB = "joints";

/** One of core's tabs in the Connections panel. */
export type ConnectionsCoreTab =
  | typeof BUILDER_TAB
  | typeof CLASHES_TAB
  | typeof JOINT_OVERLAYS_TAB
  | typeof JOINTS_REVIEW_TAB;

// The Connections panel's last picked tab, kept outside the panel so it survives
// closing it and moving it between the column, a group and a window. For the page only.
interface ConnectionBuilderTabState {
  tab: string;
  select: (tab: string) => void;
}

export const useConnectionBuilderTabStore = create<ConnectionBuilderTabState>((set) => ({
  tab: BUILDER_TAB,
  select: (tab) => set({ tab }),
}));
