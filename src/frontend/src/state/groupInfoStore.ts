import { create } from 'zustand';

export interface GroupInfo {
  name: string;
  description?: string;
  members?: string[];
  type: 'design' | 'simulation';
  fe_object_type?: 'node' | 'element';
  parent_name: string;
}

interface GroupInfoState {
  show_group_info_box: boolean;
  selectedGroup: GroupInfo | null;
  availableGroups: GroupInfo[];
  setShowGroupInfoBox: (show: boolean) => void;
  setSelectedGroup: (group: GroupInfo | null) => void;
  setAvailableGroups: (groups: GroupInfo[]) => void;
  toggle: () => void;
}

export const useGroupInfoStore = create<GroupInfoState>((set, get) => ({
  show_group_info_box: false,
  selectedGroup: null,
  availableGroups: [],
  setShowGroupInfoBox: (show) => set({ show_group_info_box: show }),
  setSelectedGroup: (group) => set({ selectedGroup: group }),
  setAvailableGroups: (groups) => set({ availableGroups: groups }),
  toggle: () => set((state) => ({ show_group_info_box: !state.show_group_info_box })),
}));