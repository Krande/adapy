// "External controls": whether THIS TAB lets an agent drive it over the REST API (`/api/viewer/...`,
// services/viewerControl). Off by default, and per tab: it is kept in sessionStorage, so it survives
// a reload of the tab that turned it on and no other tab -- a user consents for the tab they are
// looking at. Offered only where the server allows viewer control at all (`runtime.viewerControl()`,
// ADA_VIEWER_CONTROL).

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface ExternalControlsState {
  enabled: boolean;
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
      enabled: false,
      setEnabled: (v) => set({ enabled: v }),
    }),
    { name: "ada-external-controls", storage: tabStorage },
  ),
);
