import { create } from 'zustand';

// `GroupInfo` keeps its semantic name — it still describes a single group.
// The store name moved to `SceneInfo*` because the panel that consumes it
// now spans both Stats and Groups sub-sections; groups are one part of the
// scene metadata, not the whole panel.
export interface GroupInfo {
  name: string;
  description?: string;
  members?: string[];
  type: 'design' | 'simulation';
  fe_object_type?: 'node' | 'element';
  parent_name: string;
}

interface SceneInfoState {
  show_scene_info_box: boolean;
  selectedGroup: GroupInfo | null;
  availableGroups: GroupInfo[];
  setShowSceneInfoBox: (show: boolean) => void;
  setSelectedGroup: (group: GroupInfo | null) => void;
  setAvailableGroups: (groups: GroupInfo[]) => void;
  toggle: () => void;
}

export const useSceneInfoStore = create<SceneInfoState>((set, get) => ({
  show_scene_info_box: false,
  selectedGroup: null,
  availableGroups: [],
  setShowSceneInfoBox: (show) => set({ show_scene_info_box: show }),
  setSelectedGroup: (group) => set({ selectedGroup: group }),
  setAvailableGroups: (groups) => set({ availableGroups: groups }),
  toggle: () => set((state) => ({ show_scene_info_box: !state.show_scene_info_box })),
}));
