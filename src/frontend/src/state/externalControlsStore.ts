// "External controls": whether THIS TAB lets an agent drive it (`/api/viewer/...` on a hosted viewer,
// a local client through the desktop websocket server; services/viewerControl). Per tab: the choice is
// kept in sessionStorage, so it survives a reload of the tab that made it and no other tab -- a user
// decides for the tab they are looking at.
//
// Until the user chooses, the server's mode decides (ADA_VIEWER_CONTROL, `runtime.viewerControlMode()`):
// "opt-in" (a deployed server) leaves it off, "auto" (a local dev stack) turns it on. A tab switched
// off stays off whatever the mode.

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { runtime, type ViewerControlMode } from "@/runtime/config";

interface ExternalControlsState {
  /** The user's choice in this tab; null until they make one. */
  chosen: boolean | null;
  setEnabled: (v: boolean) => void;
}

/** sessionStorage where there is one (a browser), else nothing kept (tests, a worker). */
const tabStorage = createJSONStorage(() =>
  typeof sessionStorage !== "undefined"
    ? sessionStorage
    : { getItem: () => null, setItem: () => undefined, removeItem: () => undefined },
);

export const useExternalControlsStore = create<ExternalControlsState>()(
  persist(
    (set) => ({
      chosen: null,
      setEnabled: (v) => set({ chosen: v }),
    }),
    { name: "ada-external-controls", storage: tabStorage },
  ),
);

/** Whether external controls are on: the tab's choice, else on only where the server says "auto". */
export function externalControlsOn(chosen: boolean | null, mode: ViewerControlMode = runtime.viewerControlMode()): boolean {
  return chosen ?? mode === "auto";
}

/** The same, for this tab now. */
export function externalControlsEnabled(): boolean {
  return externalControlsOn(useExternalControlsStore.getState().chosen);
}
