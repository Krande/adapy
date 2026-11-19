// The clash contact (a joint of the Clashes tab's result) the user last pointed at in the 3D view --
// its marker or its contact patch clicked -- or opened from the Selected Object panel's link: what
// the Selected Object panel's Contact section describes, and what the Connections panel's joint list
// opens at.
//
// `seq` counts the requests, so pointing at the same contact again is a new request: the list opens
// its detail again even when the contact was already the store's cursor (`clashCheckStore.selectedJoint`)
// and its detail had been closed. An ordinary pick of the model ends it (`clear`). For the page only.

import { create } from "zustand";

interface ClashContactPickState {
  jointId: string | null;
  seq: number;
  pick: (jointId: string) => void;
  clear: () => void;
}

export const useClashContactPickStore = create<ClashContactPickState>((set) => ({
  jointId: null,
  seq: 0,
  pick: (jointId) => set((s) => ({ jointId, seq: s.seq + 1 })),
  clear: () => set((s) => (s.jointId === null ? s : { jointId: null })),
}));
