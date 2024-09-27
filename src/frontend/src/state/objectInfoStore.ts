import { create } from 'zustand';

type ObjectInfoState = {
  name: string | null;
  setName: (name: string | null) => void;
  faceIndex: number | null;
  setFaceIndex: (faceIndex: number | null) => void;
  jsonData: any | null;
  setJsonData: (jsonData: any | null) => void;
  isJsonViewVisible: boolean;
  setIsJsonViewVisible: (visible: boolean) => void;
  show_info_box: boolean;
  toggle: () => void;
};

export const useObjectInfoStore = create<ObjectInfoState>((set) => ({
  name: null,
  setName: (name) => set(() => ({ name })),
  faceIndex: null,
  setFaceIndex: (faceIndex) => set(() => ({ faceIndex })),
  jsonData: null,
  setJsonData: (jsonData) => set(() => ({ jsonData })),
  isJsonViewVisible: false,
  setIsJsonViewVisible: (visible) => set(() => ({ isJsonViewVisible: visible })),
  show_info_box: false,
  toggle: () => set((state) => ({ show_info_box: !state.show_info_box })),
}));
