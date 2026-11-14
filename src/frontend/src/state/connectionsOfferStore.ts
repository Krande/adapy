// Whether the connection builder has anything to offer here: on a viewer `ada dev connections`
// started, always; on a deployed studio, while the current scope offers at least one spec (the
// specs the live workers' code registers, as the admin assigned them to scopes -- see
// routes/connections.py). The Scene panel's Connection tab is shown while this holds.
//
// Checked by useConnectionBuilderBoot once somebody is signed in, and again on every scope change.

import { create } from "zustand";

export interface ConnectionsOfferState {
  /** The current scope offers at least one spec (always true for `ada dev connections`). */
  offered: boolean;
  /** How many specs it offers, for the tab's hint. */
  count: number;
  setOffer: (count: number) => void;
}

export const useConnectionsOfferStore = create<ConnectionsOfferState>()((set) => ({
  offered: false,
  count: 0,
  setOffer: (count) => set({ offered: count > 0, count }),
}));
