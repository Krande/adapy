// Scene > Overlays: which core overlays are on, and the marks last read for what is loaded
// (`@/utils/overlays/overlayModel`, drawn by `@/utils/overlays/overlayController`).

import { create } from "zustand";

import { emptyMarks, type OverlayId, type OverlayMarks } from "@/utils/overlays/overlayModel";

export type OverlayStatus = "idle" | "loading" | "ready" | "error";

interface OverlayState {
  enabled: Record<OverlayId, boolean>;
  status: OverlayStatus;
  error: string | null;
  marks: OverlayMarks;
  /** The collections whose relations were read, and those that have no relations index. */
  collections: string[];
  notIndexed: string[];
  setEnabled: (id: OverlayId, on: boolean) => void;
  setResult: (r: Partial<Pick<OverlayState, "status" | "error" | "marks" | "collections" | "notIndexed">>) => void;
}

export const useOverlayStore = create<OverlayState>((set) => ({
  enabled: { systems: false, flow: false, penetrations: false, connections: false },
  status: "idle",
  error: null,
  marks: emptyMarks(),
  collections: [],
  notIndexed: [],
  setEnabled: (id, on) => set((s) => ({ enabled: { ...s.enabled, [id]: on } })),
  setResult: (r) => set(r),
}));

export function anyOverlayOn(enabled: Record<OverlayId, boolean>): boolean {
  return Object.values(enabled).some(Boolean);
}
