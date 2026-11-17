// The Simulation panel's tab strip and tab bodies: the built-in Results tab (the FEA or glTF
// transport, the data panel) and a plugin's tab.

import React from "react";
import { isMirroredWindow } from "@/layout/sceneActions";
import SimulationDataInfoPanel from "../SimulationDataInfoPanel";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import { CORE_PANEL_IDS } from "@/layout/corePanelIds";
import { isPlacedElsewhere } from "@/layout/placedElsewhere";
import { usePlacedElsewhereVersion } from "@/layout/usePlacedElsewhere";
import { PluginPanelRegion, disablePlugin, type AdaPluginContext, type PanelSlot, type SimulationTabEntry } from "@/plugins";
import { LEGEND_TAB, MODEL_TAB, MODEL_TAB_LABEL, RESULTS_TAB, RESULTS_TAB_LABEL } from "../simPanelTabs";
import type { ResultControlsOptions } from "./types";
import { FeaModeControls } from "./FeaModeControls";
import { GltfClipControls } from "./GltfClipControls";

// Built-in Results tab (id "animation") — the FEA / GLTF transport controls, the optional
// data panel, plus the additive plugin color-field picker and any buttonless
// fem-sidebar panels (asTab panels are hoisted to their own tabs instead).
export const AnimationTab: React.FC<{
    sessionActive: boolean;
    hasResults: boolean;
    showSimData: boolean;
    onToggleData: () => void;
    display?: ResultControlsOptions;
}> = ({sessionActive, hasResults, showSimData, onToggleData, display}) => {
    // A shell that docks the data table in a group of its own takes it out of this tab.
    usePlacedElsewhereVersion();
    // (A pop-out window shows it in place: the main window's placement does not apply there.)
    const dataElsewhere = !isMirroredWindow() && isPlacedElsewhere(CORE_PANEL_IDS.feaTable);
    return (
    <div className="flex flex-col gap-2">
        {sessionActive ? (
            <FeaModeControls showSimData={showSimData} onToggleData={onToggleData} display={display} />
        ) : hasResults ? (
            <GltfClipControls showSimData={showSimData} onToggleData={onToggleData} />
        ) : (
            <p className="text-xs italic text-gray-400" data-testid="sim-no-results">
                No results loaded.
            </p>
        )}
        {showSimData && !dataElsewhere && (
            <div className="flex-1 overflow-hidden">
                <SimulationDataInfoPanel />
            </div>
        )}
        {/* Plugin-contributed FEM-sidebar panels that did NOT opt into a tab.
            Each is ErrorBoundary-wrapped; renders nothing when no plugin is
            active. `excludeTabs` keeps asTab panels out (the tab host owns them). */}
        {!isMirroredWindow() && <PluginPanelRegion region="fem-sidebar" excludeTabs />}
    </div>
    );
};

export const SimTabStrip: React.FC<{
    tabs: SimulationTabEntry[];
    /** The built-in Results tab. */
    results?: boolean;
    legendTab: boolean;
    modelTab: boolean;
    activeTab: string;
    onSelect: (id: string) => void;
    ctxFor: (pluginId: string) => AdaPluginContext;
}> = ({tabs, results = true, legendTab, modelTab, activeTab, onSelect, ctxFor}) => (
    <div
        className="flex flex-wrap gap-0.5 border-b border-white/15"
        role="tablist"
        aria-label="Simulation panel section"
    >
        {results && (
            <SimTabButton
                id={RESULTS_TAB}
                label={RESULTS_TAB_LABEL}
                active={activeTab === RESULTS_TAB}
                onClick={() => onSelect(RESULTS_TAB)}
            />
        )}
        {modelTab && (
            <SimTabButton
                id={MODEL_TAB}
                label={MODEL_TAB_LABEL}
                active={activeTab === MODEL_TAB}
                onClick={() => onSelect(MODEL_TAB)}
            />
        )}
        {legendTab && (
            <SimTabButton id={LEGEND_TAB} label="Legend" active={activeTab === LEGEND_TAB} onClick={() => onSelect(LEGEND_TAB)} />
        )}
        {tabs.map((t) => {
            let badge: number | string | null = null;
            try {
                badge = t.asTab.badge ? t.asTab.badge(ctxFor(t.pluginId)) : null;
            } catch (err) {
                disablePlugin(t.pluginId, `tab badge threw: ${String(err)}`);
                badge = null;
            }
            return (
                <SimTabButton
                    key={t.panel.id}
                    id={t.panel.id}
                    label={t.asTab.label}
                    active={activeTab === t.panel.id}
                    contextual={t.asTab.contextual}
                    badge={badge}
                    onClick={() => onSelect(t.panel.id)}
                />
            );
        })}
    </div>
);

/** One tab of a panel's tab strip: the Simulation panel's, and the connection builder's. */
export const SimTabButton: React.FC<{
    id: string;
    label: string;
    active: boolean;
    contextual?: boolean;
    badge?: number | string | null;
    onClick: () => void;
}> = ({label, active, contextual, badge, onClick}) => {
    const showBadge = badge != null && badge !== 0 && badge !== "";
    return (
        <button
            type="button"
            role="tab"
            aria-selected={active}
            onClick={onClick}
            className={
                "px-2.5 py-1.5 font-semibold whitespace-nowrap border-b-2 -mb-px flex items-center gap-1.5 text-sm " +
                (active
                    ? "border-blue-400 text-[var(--ada-panel-text)]"
                    : "border-transparent text-gray-400 hover:text-[var(--ada-panel-text)]")
            }
        >
            {contextual && <span className="w-1.5 h-1.5 rounded-full bg-violet-400" aria-hidden="true" />}
            {label}
            {showBadge && (
                <span className="ml-0.5 rounded-full bg-[var(--ada-fail,#ef4444)] text-white text-[10px] leading-none px-1.5 py-0.5">
                    {badge}
                </span>
            )}
        </button>
    );
};

// One plugin tab body inside a per-plugin ErrorBoundary — a crash disables the
// plugin for the session (mirrors PluginPanelRegion) rather than the whole panel.
export const PluginTabBody: React.FC<{pluginId: string; panel: PanelSlot; ctx: AdaPluginContext}> = ({
    pluginId,
    panel,
    ctx,
}) => (
    <ErrorBoundary
        label={`Plugin ${pluginId}`}
        fallback={(error, reset) => {
            disablePlugin(pluginId, `sim tab render threw: ${error.message}`);
            return (
                <div className="rounded-md border border-red-700/60 bg-gray-800/95 p-2 text-xs text-gray-100">
                    <div className="font-semibold text-red-300">Plugin “{pluginId}” hit an error</div>
                    <div className="mt-0.5 mb-1.5 break-words text-gray-400">{error.message}</div>
                    <button
                        type="button"
                        onClick={reset}
                        className="rounded-sm bg-gray-700 px-2 py-1 text-white hover:bg-gray-600"
                    >
                        Retry
                    </button>
                </div>
            );
        }}
    >
        {panel.render(ctx)}
    </ErrorBoundary>
);
