// SimulationControls — single panel that drives the active
// animation. Two presentation modes, picked by what session is
// active:
//
//  1. Streaming-FEA session: two sliders + play/pause/stop.
//     Slider A scrubs the step / mode index; Slider B is the
//     deformation factor (range follows analysis_kind, [0, 1] for
//     static, [-1, +1] for eigen). Play/pause/stop drive the
//     RAF-fed sweep on Slider B; Slider A stays under direct user
//     control.
//
//  2. GLTF-clip animation: the legacy path — drop-down to pick
//     among parsed clips, time scrubber, play/pause/stop driving
//     THREE.AnimationMixer.
//
// The user originally intended these buttons for the mode-step
// cycle anyway (per the FEA workflow); the GLTF-clip path stays
// as a fallback for non-FEA models.

import React, { useState } from "react";
import { useAnimationStore } from "@/state/animationStore";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { useTableNavStore } from "@/state/tableNavStore";
import { toggleDataTable } from "@/layout/resultViewActions";
import { isMirroredWindow } from "@/layout/sceneActions";
import { runtime } from "@/runtime/config";
import { makePluginContext } from "@/plugins";
import SimWindowFrame, { type SimFrameMode } from "./SimWindowFrame";
import FemConceptsPanel from "./FemConceptsPanel";
import { LEGEND_TAB, MODEL_TAB } from "./simPanelTabs";
import ColorLegend from "../viewer/ColorLegend";
import { useSimulationTabs, type SimulationTabs } from "./simulationControls/useSimulationTabs";
import type { ResultControlsOptions, SimulationBodyProps, SimulationControlsProps } from "./simulationControls/types";
import { AnimationTab, PluginTabBody, SimTabStrip } from "./simulationControls/tabs";
import { FeaModeControls } from "./simulationControls/FeaModeControls";
import { GltfClipControls } from "./simulationControls/GltfClipControls";

export { type SimulationControlsProps, type ResultControlsOptions, type SimulationBodyProps } from "./simulationControls/types";
export { useSimulationTabs, type SimulationTabs } from "./simulationControls/useSimulationTabs";
export { SimTabButton } from "./simulationControls/tabs";

/** The Simulation panel's content -- its tab strip and the active tab -- over tab state its host keeps. */
export const SimulationContent: React.FC<{sim: SimulationTabs} & ResultControlsOptions> = ({sim, ...display}) => {
    const {
        forcedTabId, stores, tabs, legendTab, modelTab, activeTab, selectTab, activePluginTab,
        sessionActive, hasResults, showSimData, togglePanel, builtin,
    } = sim;
    return (
        <div className="flex flex-col gap-2 min-w-0">
            {!forcedTabId && (builtin ? tabs.length > 0 || legendTab || modelTab : tabs.length > 1) && (
                <SimTabStrip
                    tabs={tabs}
                    results={builtin}
                    legendTab={legendTab}
                    modelTab={modelTab}
                    activeTab={activeTab}
                    onSelect={selectTab}
                    ctxFor={(pid) => makePluginContext(pid, stores)}
                />
            )}
            {!builtin && !activePluginTab ? (
                <p className="text-xs italic text-gray-400" data-testid="sim-no-tools">
                    No simulation tools for this model.
                </p>
            ) : legendTab && activeTab === LEGEND_TAB ? (
                <ColorLegend placement="panel" />
            ) : modelTab && activeTab === MODEL_TAB ? (
                <FemConceptsPanel />
            ) : activePluginTab ? (
                <PluginTabBody
                    pluginId={activePluginTab.pluginId}
                    panel={activePluginTab.panel}
                    ctx={makePluginContext(activePluginTab.pluginId, stores)}
                />
            ) : (
                <AnimationTab
                    sessionActive={sessionActive}
                    hasResults={hasResults}
                    showSimData={showSimData}
                    onToggleData={togglePanel}
                    display={display}
                />
            )}
        </div>
    );
};

/** The Simulation panel's content, with its own tab state. */
export const SimulationBody: React.FC<SimulationBodyProps> = ({forcedTabId, excludeDockedTabs, builtinTabs, ...display}) => (
    <SimulationContent sim={useSimulationTabs(forcedTabId, {excludeDockedTabs, builtinTabs})} {...display}/>
);

/** The result controls on their own -- the FEA transport and pickers, or the GLTF clip
 *  controls -- without the tab strip, the data table or the plugin panels. Nothing when
 *  no results are loaded. */
export const AnimationControls: React.FC<ResultControlsOptions> = (display) => {
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const hasAnimation = useAnimationStore((s) => s.hasAnimation);
    const showSimData = useTableNavStore((s) => s.isPanelOpen);
    if (sessionActive) {
        return <FeaModeControls showSimData={showSimData} onToggleData={toggleDataTable} display={display} />;
    }
    return hasAnimation ? <GltfClipControls showSimData={showSimData} onToggleData={toggleDataTable} /> : null;
};

/** The classic frame: `SimWindowFrame`, docked in the menu column, floating, or a whole window. */
const SimulationControls: React.FC<SimulationControlsProps> = ({initialMode = "docked", forcedTabId}) => {
    const sim = useSimulationTabs(forcedTabId);
    const [mode, setMode] = useState<SimFrameMode>(initialMode);

    return (
        // A follower window shares state over same-origin BroadcastChannel + a
        // window.open() URL built from this tab's own location (see
        // utils/simChannel.ts's followerUrl) -- neither works from a file:// tab
        // (every `file:` document is its own unique origin), so the button is
        // omitted there rather than offered and failing.
        <SimWindowFrame mode={mode} setMode={setMode} onOpenWindow={runtime.isFileOrigin() || isMirroredWindow() ? undefined : sim.onOpenWindow}>
            <SimulationContent sim={sim}/>
        </SimWindowFrame>
    );
};

export default SimulationControls;
