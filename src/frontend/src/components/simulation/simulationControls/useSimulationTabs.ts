// The Simulation panel's tabs: the built-in Results tab and the plugins' tabs, which one is
// active, and what the session offers.

import { useAnimationStore } from "@/state/animationStore";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { useTableNavStore } from "@/state/tableNavStore";
import { toggleDataTable } from "@/layout/resultViewActions";
import { isMirroredWindow } from "@/layout/sceneActions";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { followerUrl } from "@/utils/simChannel";
import { useViewerStores } from "@/state/AdaViewerContext";
import { usePlacedElsewhereVersion } from "@/layout/usePlacedElsewhere";
import { makePluginContext, getSimulationTabs, findSimulationTabById } from "@/plugins";
import { LEGEND_TAB, MODEL_TAB, RESULTS_TAB, feModelLoaded, hasFemConcepts, resolveSimTab, simTabKey, withoutDockedTabs } from "../simPanelTabs";
import { useFemConceptsStore } from "@/state/femConceptsStore";
import { useSimPanelStore } from "@/state/simPanelStore";
import { useColorStore } from "@/state/colorLegendStore";
import { useIsMobile } from "@/utils/useIsMobile";

/**
 * The Simulation panel's tab state: which tabs exist (results, the FE model, the
 * phone legend, plugin tabs), which one is active, and how to open the active one
 * in a follower window. The frame needs the last for its header, the content the
 * rest, so both take it from here.
 */
export function useSimulationTabs(forcedTabId?: string, opts: {excludeDockedTabs?: boolean; builtinTabs?: boolean} = {}) {
    const builtin = opts.builtinTabs !== false;
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    // Panel-visibility now lives in tableNavStore so external
    // triggers (ObjectInfoBoxComponent's "Show in data" button) can
    // open the panel without prop-drilling. Local state would still
    // work for the button-toggle case but breaks the cross-component
    // open-from-info-panel flow.
    const showSimData = useTableNavStore((s) => s.isPanelOpen);
    const togglePanel = toggleDataTable;

    const stores = useViewerStores();

    // Plugin-contributed Simulation tabs (fem-sidebar panels carrying `asTab`).
    // Rebuilt each render so activation + badges track live store state.
    // A tab the layout shows in a group or window of its own leaves the strip.
    usePlacedElsewhereVersion();
    const baseCtx = makePluginContext("", stores);
    // A pop-out window shows the Results tab alone: the plugin tabs, the FE model and the phone
    // legend read the scene, which only the main window has.
    const mirrored = isMirroredWindow();
    const allTabs = mirrored ? [] : getSimulationTabs(baseCtx);
    const tabs = opts.excludeDockedTabs ? withoutDockedTabs(allTabs) : allTabs;
    // Phones: the colour legend is a tab here rather than floating over the canvas.
    const isMobile = useIsMobile();
    const legendShown = useColorStore((s) => s.showLegend);
    const legendTab = !mirrored && builtin && !forcedTabId && isMobile && sessionActive && legendShown;
    // The FE model tab: masses, supports, constraints, load scenarios.
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const femHasConcepts = useFemConceptsStore(hasFemConcepts);
    const modelTab = !mirrored && builtin && !forcedTabId && feModelLoaded({manifest, femHasConcepts});

    // The last pick, for the model it was made on; a tab that went away (model
    // unloaded, plugin deactivated) falls back to the default.
    const sourceName = useFeaAnimationStore((s) => s.sourceName);
    const hasAnimation = useAnimationStore((s) => s.hasAnimation);
    const hasResults = sessionActive || hasAnimation;
    const tabKey = simTabKey(sourceName, hasResults);
    const remembered = useSimPanelStore((s) => s.remembered);
    const selectTab = (tab: string) => useSimPanelStore.getState().remember(tab, tabKey);
    const resolved = resolveSimTab(
        remembered,
        tabKey,
        (tab) =>
            (builtin && tab === RESULTS_TAB)
            || (tab === MODEL_TAB && modelTab)
            || (tab === LEGEND_TAB && legendTab)
            || tabs.some((t) => t.panel.id === tab),
        {hasResults, hasModel: modelTab},
    );
    // Without the built-in tabs, the first plugin tab stands in for Results.
    const activeTab = forcedTabId ?? (builtin || resolved !== RESULTS_TAB ? resolved : (tabs[0]?.panel.id ?? ""));

    const onOpenWindow = () => {
        // The built-in tabs need this window's scene; the follower opens on a plugin tab.
        const builtIn = activeTab === RESULTS_TAB || activeTab === MODEL_TAB || activeTab === LEGEND_TAB;
        const panelId = forcedTabId ?? (!builtIn ? activeTab : tabs[0]?.panel.id ?? "");
        const source = useFeaAnimationStore.getState().sourceName || "model";
        const scope = scopeUrlPart(useScopeStore.getState().current);
        window.open(followerUrl(source, panelId, scope), "_blank", "noopener,width=920,height=820");
    };

    // Forced-tab hosts (new-window follower / maximized window) are canvas-less
    // and load only result sidecars, so the plugin's activation predicate can be
    // false and `tabs` empty even though the panel's data is present. Fall back to
    // an activation-ignoring lookup by id so the follower mounts the real plugin
    // controls instead of silently dropping to the Results tab.
    const activePluginTab =
        tabs.find((t) => t.panel.id === activeTab) ??
        (forcedTabId ? findSimulationTabById(forcedTabId) : null);

    return {
        forcedTabId, stores, tabs, legendTab, modelTab, activeTab, selectTab, activePluginTab,
        sessionActive, hasResults, showSimData, togglePanel, onOpenWindow, builtin,
    };
}

export type SimulationTabs = ReturnType<typeof useSimulationTabs>;
