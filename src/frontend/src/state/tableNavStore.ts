// Tiny slice for FEA data-table ↔ 3D-scene navigation. Two pieces
// of state:
//
//   * ``activeNodeId`` — the node id currently "spotlighted" in the
//     table (background highlight on that row) and in the 3D scene
//     (marker + camera frame, managed by ``goToNode``). Single id
//     since the table is in a single-selection flow.
//   * ``goToTarget`` — a pending request to scroll the virtualizer
//     to a specific id, fired from outside the table (Phase 1b:
//     ObjectInfoBoxComponent's "Show in data" button). Consumed
//     and cleared once the table acts on it.

import {create} from "zustand";

export type TableNavTarget = {kind: "node"; id: number};

export interface TableNavState {
    activeNodeId: number | null;
    setActiveNodeId: (id: number | null) => void;

    goToTarget: TableNavTarget | null;
    setGoToTarget: (t: TableNavTarget | null) => void;
}

export const useTableNavStore = create<TableNavState>((set) => ({
    activeNodeId: null,
    setActiveNodeId: (activeNodeId) => set({activeNodeId}),
    goToTarget: null,
    setGoToTarget: (goToTarget) => set({goToTarget}),
}));
