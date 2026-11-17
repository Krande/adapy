// Props shared by the Simulation panel's pieces: where the result controls are laid out and
// which of their toggles show, for the panel, its body and the docked controls.

import type { SimFrameMode } from "../SimWindowFrame";

export interface SimulationControlsProps {
    // The follower / new-window entry boots the frame already maximized as a
    // full window and locks it to a single plugin tab (no tab strip, no
    // animation tab). Omitted for the normal docked panel.
    initialMode?: SimFrameMode;
    forcedTabId?: string;
}

/** How the result controls are drawn. Every field absent is the classic panel. */
export interface ResultControlsOptions {
    /** `"inline"`: one toolbar row, the visualisation options always shown. Default `"panel"`. */
    layout?: "panel" | "inline";
    /** The "Warp by displacement" toggle. Default true. */
    showWarpToggle?: boolean;
    /** The "Beams as solid" and "Solid edges" toggles. Default true. */
    showBeamSolids?: boolean;
    /** A result-colouring on/off toggle beside the colormap. Default false. */
    showColorToggle?: boolean;
}

export interface SimulationBodyProps extends ResultControlsOptions {
    forcedTabId?: string;
    /** Leave out plugin tabs whose panel names a `dock`, for a shell that docks them itself. */
    excludeDockedTabs?: boolean;
    /**
     * The built-in Results, FE model and Legend tabs. False: plugin tabs only, for a
     * shell that has the result controls elsewhere (`AnimationControls` in a toolbar).
     * Default true.
     */
    builtinTabs?: boolean;
}

export interface ControlPanelProps {
    showSimData: boolean;
    onToggleData: () => void;
}
