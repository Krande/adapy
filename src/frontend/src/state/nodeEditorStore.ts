// src/state/nodeEditorStore.ts

import {create} from 'zustand';

type NodeEditorState = {
  isNodeEditorVisible: boolean;
  setIsNodeEditorVisible: (visible: boolean) => void;
};

export const useNodeEditorStore = create<NodeEditorState>((set) => ({
  isNodeEditorVisible: false,
  setIsNodeEditorVisible: (visible) => set({ isNodeEditorVisible: visible }),
}));