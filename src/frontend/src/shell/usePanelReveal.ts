import React from "react";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";

// Business logic asking the shell to reveal a panel.
//
// This replaces `useLegacyFlagSync`, and it runs the other way round.
//
// That bridge existed because panels gated themselves on a store boolean — in the classic
// UI those flags WERE the visibility model, so a docked panel that did not have its flag
// set rendered an empty box. The shell had to keep the flags in step with the dock. It
// was always meant to come out at the cutover, and the cutover has happened.
//
// What could not simply be deleted is the intent underneath. `cellBuilderStore` sets
// `panelVisible: true` from `openModel`, `focusSystem` and `revealEquipment` — not as
// "this panel is visible" but as "the user just did something that needs the Builder on
// screen". Dropping the flag entirely would have made "focus this system" quietly do
// nothing when the panel was closed.
//
// So the direction inverts: the panel no longer gates itself, the dock decides visibility
// as it does for every other panel, and this watches the flag going TRUE and opens the
// dock panel. The store asks; the shell answers.
//
// Rising edge only. False means "the model closed", and the Builder panel showing its own
// empty state is better than a panel that vanishes out from under you — the same reason
// mode switching never unloads anything.

export function usePanelReveal(): void {
    React.useEffect(() => {
        let prev = useCellBuilderStore.getState().panelVisible;
        return useCellBuilderStore.subscribe((s) => {
            const now = s.panelVisible;
            if (now && !prev) {
                const {mode} = useModeStore.getState();
                useLayoutStore.getState().openPanel(mode, "cellbuilder", "right");
            }
            prev = now;
        });
    }, []);
}
