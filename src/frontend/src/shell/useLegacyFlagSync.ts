import React from "react";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useTableNavStore} from "@/state/tableNavStore";
import {useLayoutStore, type DockState, type ModeLayout} from "./layoutStore";
import {useModeStore} from "./modeStore";
import type {PanelId} from "./panelRegistry";

// Mirrors dock state into the legacy visibility booleans.
//
// Several panels still gate themselves on a store flag — `CellBuilderPanel` returns null
// unless `cellBuilderStore.panelVisible`, `SimulationDataInfoPanel` unless
// `tableNavStore.isPanelOpen`. In the classic UI those flags WERE the visibility model;
// in the shell the dock is. Without this bridge a docked panel renders an empty box, and
// the cause is invisible — the panel is mounted, it just decided not to draw.
//
// Deliberately a bridge, not a rewrite. Deleting those flags means editing business-logic
// components under the fence, and external callers still set them directly — the
// Properties panel's "Show in data" opens the table by flipping `isPanelOpen`. So the
// shell keeps them in step and they come out at M8 cutover, together with the classic UI
// that needs them.
//
// One-way (layout → flag) on purpose. Two-way sync between a boolean and a layout tree
// invites a feedback loop; the reverse direction is handled where it belongs, by
// `togglePanel` in the actions that own each panel.

/** Panels whose legacy flag has to follow their dock visibility. */
const FLAG_PANELS: PanelId[] = ["cellbuilder", "fea-table"];

/** Is this panel visible right now — in an expanded dock, floating, or as an overlay? */
function isPanelVisible(layout: ModeLayout | undefined, panel: PanelId): boolean {
    if (!layout) return false;
    for (const dock of Object.values(layout.docks) as DockState[]) {
        if (!dock.collapsed && dock.tabs.includes(panel)) return true;
    }
    return panel in layout.floats || layout.overlays[panel] === true;
}

export function useLegacyFlagSync(): void {
    const mode = useModeStore((s) => s.mode);
    const layout = useLayoutStore((s) => s.perMode[mode]);

    React.useEffect(() => {
        const visible = Object.fromEntries(
            FLAG_PANELS.map((p) => [p, isPanelVisible(layout, p)]),
        ) as Record<PanelId, boolean>;

        // Only write when the value actually differs: these stores drive re-renders of
        // large panels, and an unconditional set on every layout change would churn them.
        const cb = useCellBuilderStore.getState();
        if (cb.panelVisible !== visible.cellbuilder) {
            useCellBuilderStore.setState({panelVisible: visible.cellbuilder});
        }

        const nav = useTableNavStore.getState();
        if (nav.isPanelOpen !== visible["fea-table"]) {
            nav.setPanelOpen(visible["fea-table"]);
        }
    }, [layout]);
}
