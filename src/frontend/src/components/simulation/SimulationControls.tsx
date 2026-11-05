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

import React, {useEffect, useMemo, useRef, useState, useSyncExternalStore} from "react";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useTableNavStore} from "@/state/tableNavStore";
import {requestRender, usePerfStore} from "@/state/perfStore";

import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {COLORMAP_NAMES} from "@/utils/scene/fea/colormaps";
import ResultScaleSettings from "./ResultScaleSettings";
import AnchoredPopover from "../common/AnchoredPopover";
import {resetFeaAnimationPhase} from "@/utils/scene/fea/feaAnimationDriver";
import {buildFeaPropertyFields, buildFeaResultHierarchy} from "@/utils/scene/fea/resultHierarchy";
import {unbakedStepsNote} from "@/utils/scene/fea/bakedSteps";
import {caseStatusText, hasLazyCases, mergeCaseSteps} from "@/utils/scene/fea/caseSteps";
import {caseStatus, caseStatusVersion, subscribeCaseStatus} from "@/services/fea/feaCaseResolver";
import {availableResultLayers} from "@/utils/scene/fea/resultLayers";
import {selectedResultUnit} from "@/utils/scene/fea/resultUnits";
import {
    selectFeaResultComponent,
    selectFeaResultLayer,
} from "@/utils/scene/fea/resultSelection";
import {
    load_fea_streaming,
    setBeamSolidsVisible as setBeamSolidsVisibleScene,
    setFeaResultColorsVisible,
    setFeaResultPointMarkersVisible,
    syncFeaOverlayVisibility,
} from "@/utils/scene/handlers/load_fea_streaming";
import {fieldHasResultPointMarkers} from "@/utils/scene/fea/resultPointMarkers";
import {beamSolidsToggleState} from "@/utils/scene/fea/streaming/beamSolidsToggle";
import {followerUrl} from "@/utils/simChannel";
import {runtime} from "@/runtime/config";
import GearIcon from "../icons/GearIcon";
import PlayPauseIcon from "../icons/PlayPauseIcon";
import StopIcon from "../icons/StopIcon";
import SimulationDataInfoPanel from "./SimulationDataInfoPanel";
import FEMDataPanelIcon from "../icons/FEMDataPanelIcon";
import AnimationExportButton from "./AnimationExportButton";
import {FIELD_CONTROL, TRANSPORT_BUTTON, TRANSPORT_BUTTON_ON} from "./controlStyles";
import DeformScaleIcon from "../icons/DeformScaleIcon";
import {formatStepTime} from "@/utils/scene/fea/timeHistory";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {useViewerRefs, useViewerStores} from "@/state/AdaViewerContext";
import {usePlacedElsewhereVersion} from "@/layout/usePlacedElsewhere";
import {
    PluginPanelRegion,
    makePluginContext,
    getSimulationTabs,
    findSimulationTabById,
    disablePlugin,
    type AdaPluginContext,
    type PanelSlot,
    type SimulationTabEntry,
} from "@/plugins";
import SimWindowFrame, {type SimFrameMode} from "./SimWindowFrame";
import FemConceptsPanel from "./FemConceptsPanel";
import {
    LEGEND_TAB,
    MODEL_TAB,
    MODEL_TAB_LABEL,
    RESULTS_TAB,
    RESULTS_TAB_LABEL,
    feModelLoaded,
    hasFemConcepts,
    resolveSimTab,
    simTabKey,
    withoutDockedTabs,
} from "./simPanelTabs";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useSimPanelStore} from "@/state/simPanelStore";
import ColorLegend from "../viewer/ColorLegend";
import {useColorStore} from "@/state/colorLegendStore";
import {useIsMobile} from "@/utils/useIsMobile";

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
    const togglePanel = useTableNavStore((s) => s.togglePanel);

    const stores = useViewerStores();

    // Plugin-contributed Simulation tabs (fem-sidebar panels carrying `asTab`).
    // Rebuilt each render so activation + badges track live store state.
    // A tab the layout shows in a group or window of its own leaves the strip.
    usePlacedElsewhereVersion();
    const baseCtx = makePluginContext("", stores);
    const allTabs = getSimulationTabs(baseCtx);
    const tabs = opts.excludeDockedTabs ? withoutDockedTabs(allTabs) : allTabs;
    // Phones: the colour legend is a tab here rather than floating over the canvas.
    const isMobile = useIsMobile();
    const legendShown = useColorStore((s) => s.showLegend);
    const legendTab = builtin && !forcedTabId && isMobile && sessionActive && legendShown;
    // The FE model tab: masses, supports, constraints, load scenarios.
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const femHasConcepts = useFemConceptsStore(hasFemConcepts);
    const modelTab = builtin && !forcedTabId && feModelLoaded({manifest, femHasConcepts});

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
    const togglePanel = useTableNavStore((s) => s.togglePanel);
    if (sessionActive) {
        return <FeaModeControls showSimData={showSimData} onToggleData={togglePanel} display={display} />;
    }
    return hasAnimation ? <GltfClipControls showSimData={showSimData} onToggleData={togglePanel} /> : null;
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
        <SimWindowFrame mode={mode} setMode={setMode} onOpenWindow={runtime.isFileOrigin() ? undefined : sim.onOpenWindow}>
            <SimulationContent sim={sim}/>
        </SimWindowFrame>
    );
};

// Built-in Results tab (id "animation") — the FEA / GLTF transport controls, the optional
// data panel, plus the additive plugin color-field picker and any buttonless
// fem-sidebar panels (asTab panels are hoisted to their own tabs instead).
const AnimationTab: React.FC<{
    sessionActive: boolean;
    hasResults: boolean;
    showSimData: boolean;
    onToggleData: () => void;
    display?: ResultControlsOptions;
}> = ({sessionActive, hasResults, showSimData, onToggleData, display}) => (
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
        {showSimData && (
            <div className="flex-1 overflow-hidden">
                <SimulationDataInfoPanel />
            </div>
        )}
        {/* Plugin-contributed FEM-sidebar panels that did NOT opt into a tab.
            Each is ErrorBoundary-wrapped; renders nothing when no plugin is
            active. `excludeTabs` keeps asTab panels out (the tab host owns them). */}
        <PluginPanelRegion region="fem-sidebar" excludeTabs />
    </div>
);

const SimTabStrip: React.FC<{
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

const SimTabButton: React.FC<{
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
const PluginTabBody: React.FC<{pluginId: string; panel: PanelSlot; ctx: AdaPluginContext}> = ({
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

interface ControlPanelProps {
    showSimData: boolean;
    onToggleData: () => void;
}

const FeaModeControls: React.FC<ControlPanelProps & {display?: ResultControlsOptions}> = ({onToggleData, display = {}}) => {
    const {layout = "panel", showWarpToggle = true, showBeamSolids = true, showColorToggle = false} = display;
    const inline = layout === "inline";
    const {
        mesh,
        range,
        factor,
        period,
        isPlaying,
        stepIndex,
        nSteps,
        sourceName,
        manifest,
        fieldName,
        reduction,
        colormap,
        warpEnabled,
        scaleFactor,
        layer,
        ipReduction,
        nodalAverage,
        applyStep,
        setFactor,
        setPeriod,
        setIsPlaying,
        setStepIndex,
        setColormap,
        setWarpEnabled,
        setScaleFactor,
        setIpReduction,
        setNodalAverage,
        beamSolidsVisible,
        setBeamSolidsVisible,
        beamSolidsUnavailable,
        beamSolidEdgesVisible,
        setBeamSolidEdgesVisible,
        resultPointMarkersVisible,
        timeHistory,
        envelopeMode,
        setEnvelopeMode,
        startFramePreload,
        framePreload,
        resultColorsVisible,
        setResultColorsVisible,
    } = useFeaAnimationStore();
    const onResultColorsToggle = (next: boolean) => {
        setResultColorsVisible(next);
        setFeaResultColorsVisible(next);
    };

    const edgeFade = usePerfStore((s) => s.edgeFade);
    const edgeFadeFullPx = usePerfStore((s) => s.edgeFadeFullPx);
    const onEdgeFadeChange = (on: boolean, fullPx: number) => {
        const perf = usePerfStore.getState();
        perf.setEdgeFade(on);
        perf.setEdgeFadeFullPx(fullPx);
        requestRender();
    };
    const beamSolidsToggle = beamSolidsToggleState(manifest, beamSolidsUnavailable);
    const onToggleBeamSolidEdges = (next: boolean) => {
        setBeamSolidEdgesVisible(next);
        syncFeaOverlayVisibility();
    };
    const onToggleBeamSolids = (next: boolean) => {
        setBeamSolidsVisible(next);
        setBeamSolidsVisibleScene(next);
    };
    // Options panel toggle — currently houses just the colormap
    // picker. Kept folded by default so the controls row stays
    // single-line; new per-session preferences (background tone,
    // scalar-bar tick density, etc.) land in here without adding
    // top-level buttons.
    const [showOptions, setShowOptions] = useState(false);
    // The colour scale's own settings — range, bands, ramp. Beside the colormap
    // dropdown rather than inside it, because a dropdown can pick a ramp and
    // nothing else, and the range is the half of the decision that makes two load
    // cases comparable.
    const [showScale, setShowScale] = useState(false);
    // Inline there is no room to reveal into: the options are always shown, and the
    // scale settings hang off their button in a popover.
    const optionsOpen = inline || showOptions;
    const scaleAnchorRef = useRef<HTMLButtonElement>(null);

    const [lo, hi] = range;
    // Step granularity for the factor slider — 200 stops over the
    // active range is well below human-visible jumps.
    const factorStep = Math.max((hi - lo) / 200, 0.001);

    // Active field metadata. Drives the reduction dropdown options
    // (magnitude only available for vector fields) and the step
    // slider's max.
    const activeField = useMemo(() => {
        if (!manifest || !fieldName) return null;
        return manifest.fields.find((f) => f.name_canonical === fieldName) ?? null;
    }, [manifest, fieldName]);
    const fieldPickerValue = activeField?.surface_variants?.[0]?.field_name
        ?? fieldName
        ?? "";
    const activeUnit = selectedResultUnit(activeField, reduction);
    const stepValues = useMemo(() => activeField?.steps.map((s) => s.value) ?? [], [activeField]);
    const stepTimeText = timeHistory
        ? formatStepTime(activeField?.steps[stepIndex]?.value ?? 0, stepValues)
        : "";
    const timeReadoutChars = timeHistory && stepValues.length
        ? formatStepTime(Math.max(...stepValues.map(Math.abs)), stepValues).length
        : 0;

    const reductionOptions = useMemo<string[]>(() => {
        if (!activeField) return [];
        const out: string[] = [];
        if (!activeField.group_path && activeField.kind.startsWith("vector")) out.push("magnitude");
        for (const c of activeField.components) out.push(c);
        return out;
    }, [activeField]);

    const fieldHierarchy = useMemo(
        () => buildFeaResultHierarchy(manifest?.fields ?? []),
        [manifest],
    );
    // Colour by what the elements ARE (superelement, material, section, thickness):
    // listed apart from the results, after them.
    const propertyFields = useMemo(() => buildFeaPropertyFields(manifest?.fields ?? []), [manifest]);
    // The step list: stored steps, then (bake_version 4) the load combinations
    // the bake left to compute on request, each with its status.
    const lazyCases = hasLazyCases(manifest);
    const stepSlots = useMemo(() => mergeCaseSteps(manifest, activeField), [manifest, activeField]);
    const caseStatusTick = useSyncExternalStore(subscribeCaseStatus, caseStatusVersion, caseStatusVersion);
    const slotStatus = useMemo(() => {
        void caseStatusTick;
        const out = new Map<number, string>();
        if (!sourceName) return out;
        for (const s of stepSlots) {
            if (s.kind !== "combination" || !s.recipeHash) continue;
            out.set(s.slot, caseStatusText(caseStatus(sourceName, {n: s.value, recipe_hash: s.recipeHash})));
        }
        return out;
    }, [stepSlots, sourceName, caseStatusTick]);
    const activeSlot = stepSlots[stepIndex];
    // A bake of chosen steps (or of the model only) leaves cases out, and the
    // pickers below cannot list what is not there: say so, once. A bake that
    // offers its combinations on request lists them instead, so it needs no note.
    const unbakedNote = useMemo(() => (lazyCases ? null : unbakedStepsNote(manifest)), [manifest, lazyCases]);

    // Element-field path: expose Surface/layer and IP reduction pickers when
    // the active field has per_type buckets. Exact selectable surfaces omit
    // the generic "all" reduction because mixing fibres is misleading.
    const isElemField = !!(activeField?.per_type && activeField.per_type.length > 0);
    const layerOptions = useMemo<string[]>(
        () => activeField && isElemField ? availableResultLayers(activeField) : [],
        [activeField, isElemField],
    );
    const hasExactSurface = activeField?.surface === "selectable";
    const showIpReduction = isElemField
        && activeField?.support !== "element_nodal"
        && activeField?.support !== "element_average";
    const ipReductionOptions = ["max_abs", "max", "min", "mean"];

    // Composite morph influence = slider factor × user-set scale.
    // Captured in a helper so every load_fea_streaming call site
    // computes it the same way; missing one would leave a stale
    // unscaled morph after a field / reduction / step change.
    const morphInfluence = factor * scaleFactor;

    const onFieldChange = (newFieldName: string) => {
        void selectFeaResultComponent(newFieldName);
    };

    const onReductionChange = (newReduction: string) => {
        if (!sourceName || !manifest || !fieldName) return;
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction: newReduction,
            displacementScale: morphInfluence,
        });
    };

    const onPlay = () => {
        setIsPlaying(true);
        // A time history: load every frame into memory in the background (as many
        // as the budget holds) so play stops waiting on the network after the first
        // few frames instead of after the first loop.
        if (timeHistory) startFramePreload?.();
    };
    const onPause = () => {
        setIsPlaying(false);
        // Playing may be showing a recorded frame (fea/playbackFrames): repaint it the
        // ordinary way, so picking, the probe and the legend describe exactly it.
        if (timeHistory) void applyStep?.(stepIndex);
    };
    const onStop = () => {
        setIsPlaying(false);
        if (timeHistory) {
            // A player's stop: back to the first frame, deformation scale untouched.
            resetFeaAnimationPhase();
            onStepChange(0);
            return;
        }
        setFactor(lo === 0 ? 0 : 0); // both ranges include 0
        if (mesh && mesh.morphTargetInfluences) {
            mesh.morphTargetInfluences[0] = 0;
        }
        resetFeaAnimationPhase();
    };

    // Manual factor drag: write through to the morph influence
    // immediately. The RAF driver only runs while playing, so a
    // direct write here is the right hook for paused scrubs. Morph
    // value carries both the slider's factor and the scaleFactor
    // amplifier; the RAF driver multiplies the same way on each
    // tick.
    const onFactorChange = (newFactor: number) => {
        setFactor(newFactor);
        if (mesh && mesh.morphTargetInfluences) {
            mesh.morphTargetInfluences[0] = newFactor * scaleFactor;
        }
    };

    const onScaleFactorChange = (raw: number) => {
        // Guard NaN from an in-progress edit (user clearing the
        // input field). The store keeps the last valid value until
        // they finish typing.
        if (!isFinite(raw)) return;
        setScaleFactor(raw);
        if (mesh && mesh.morphTargetInfluences) {
            mesh.morphTargetInfluences[0] = factor * raw;
        }
    };

    const onStepChange = (newStep: number) => {
        setStepIndex(newStep);
        if (applyStep) {
            // Fire-and-forget: errors surface in the picker's own
            // error state (the callback is registered there). The
            // slider doesn't have an error display of its own.
            void applyStep(newStep);
        }
    };

    // Warp toggle re-runs load_fea_streaming so the warp-source
    // resolution (in load_fea_streaming.resolveWarpSource) re-fires
    // and the morph attribute updates without a re-fetch of the
    // color field. Cheap — the field blob is cached.
    const onWarpToggle = (next: boolean) => {
        setWarpEnabled(next);
        if (!sourceName || !manifest || !fieldName) return;
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction,
            displacementScale: morphInfluence,
        });
    };

    // Layer + IP-reduction change. Re-runs load_fea_streaming on the
    // current (field, step, reduction) so the AFEL kernel re-runs
    // with the new reduction parameters. Blob is cached so this is
    // CPU-only after the first apply.
    const onLayerChange = (next: string) => {
        void selectFeaResultLayer(next);
    };

    const onIpReductionChange = (next: string) => {
        setIpReduction(next);
        if (!sourceName || !manifest || !fieldName) return;
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction,
            displacementScale: morphInfluence,
            colormap,
        });
    };

    // Scale over every load combination (the envelope) vs the shown case.
    // Repaints the current step; the loader falls back to the case's own range,
    // labelled as such, when the server offers no envelope.
    const onEnvelopeToggle = (next: boolean) => {
        setEnvelopeMode(next);
        if (!sourceName || !manifest || !fieldName) return;
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction,
            displacementScale: morphInfluence,
            colormap,
        });
    };

    const onNodalAverageToggle = (next: boolean) => {
        setNodalAverage(next);
        if (!sourceName || !manifest || !fieldName) return;
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction,
            displacementScale: morphInfluence,
            colormap,
        });
    };

    const onColormapChange = (next: string) => {
        // Update the store first so the applyStep closure (which
        // reads colormap off the store at call time) sees the new
        // value, then re-run load_fea_streaming on the current
        // (field, step, reduction) so the mesh re-colours without
        // re-fetching the blob. Cheap — applyFieldToMesh is the only
        // work that runs since active.sourceName matches.
        setColormap(next);
        if (!sourceName || !manifest || !fieldName) return;
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction,
            displacementScale: morphInfluence,
            colormap: next,
        });
    };

    // Laid out by the panel's OWN width, not the browser window's: no window
    // breakpoints, rows wrap when they must. In a document the window is wide
    // while the panel sits in a 3D figure a few hundred pixels across. Not a
    // size container either: that zeroes the content's intrinsic width, and the
    // docked frame sizes itself to it.
    // Inline: one row that never wraps -- a toolbar that grows a line moves every control
    // below it; the host scrolls it instead.
    return (
        <div className={inline ? "flex flex-row flex-nowrap items-center gap-x-3 min-w-0 text-xs text-white" : "flex flex-col gap-2 min-w-0"}>
            {/* Row 1 — what you are looking at: Field / Comp / Step, packed from the left at
                one spacing. Field and Step share the spare width (``flex-1``) -- their names
                are the long ones -- and Comp keeps its natural width; ``min-w-0 truncate``
                lets a long field name ellipsise rather than push the row past the panel,
                and the row wraps when even that is not enough. (It used to spread the three
                with ``justify-between``, which on a wide panel left wide gaps between them.) */}
            {manifest && (
                <div className={inline ? "flex flex-row flex-nowrap items-center gap-x-3 min-w-0 shrink-0" : "flex flex-row flex-wrap items-center gap-x-3 gap-y-1 w-full min-w-0 text-xs text-white"}>
                    <label className={inline ? "flex items-center gap-1 shrink-0" : "flex items-center gap-1 min-w-0 flex-1"}>
                        <span className="text-gray-300 shrink-0">Field</span>
                        <select
                            className={inline ? `${FIELD_CONTROL} truncate` : `${FIELD_CONTROL} min-w-0 w-full truncate`}
                            style={inline ? {width: 128} : undefined}
                            value={fieldPickerValue}
                            onChange={(e) => onFieldChange(e.target.value)}
                        >
                            {/* Both nodal (AFBL blob) and element
                                (AFEL per_type) fields are renderable;
                                the picker doesn't filter — branching
                                happens in load_fea_streaming based on
                                ``field.per_type``. */}
                            {fieldHierarchy.positions.map((position) => (
                                <optgroup key={position.label} label={position.label}>
                                    {position.attributes.map(({label, field}) => (
                                        <option key={field.name_canonical} value={field.name_canonical}>
                                            {label}
                                        </option>
                                    ))}
                                </optgroup>
                            ))}
                            {fieldHierarchy.ungrouped.length > 0 && (
                                <optgroup label={fieldHierarchy.positions.length ? "Other" : "Fields"}>
                                    {fieldHierarchy.ungrouped.map((f) => (
                                        <option key={f.name_canonical} value={f.name_canonical}>
                                            {f.name_canonical}
                                        </option>
                                    ))}
                                </optgroup>
                            )}
                            {propertyFields.length > 0 && (
                                <optgroup label="Model">
                                    {propertyFields.map(({label, field}) => (
                                        <option key={field.name_canonical} value={field.name_canonical}>
                                            {label}
                                        </option>
                                    ))}
                                </optgroup>
                            )}
                        </select>
                    </label>
                    {reductionOptions.length > 0 && (
                        <label className="flex items-center gap-1 min-w-0 shrink-0">
                            <span className="text-gray-300 shrink-0">
                                Comp{activeUnit ? ` [${activeUnit}]` : ""}
                            </span>
                            <select
                                className={`${FIELD_CONTROL} min-w-0 truncate`}
                                value={reduction}
                                onChange={(e) => onReductionChange(e.target.value)}
                            >
                                {reductionOptions.map((opt) => (
                                    <option key={opt} value={opt}>
                                        {opt}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                    {activeField && nSteps > 0 && (
                        <label className={inline ? "flex items-center gap-1 shrink-0" : "flex items-center gap-1 min-w-0 flex-1"}>
                            <span className="text-gray-300 shrink-0">Step</span>
                            <select
                                className={inline ? `${FIELD_CONTROL} truncate` : `${FIELD_CONTROL} min-w-0 w-full truncate`}
                                style={inline ? {width: 144} : undefined}
                                value={stepIndex}
                                disabled={nSteps <= 1}
                                onChange={(e) => onStepChange(parseInt(e.target.value, 10))}
                                title={`Step ${stepIndex + 1} of ${nSteps}`}
                            >
                                {/* The case NAME when the reader knows one. A Sesam deck
                                    names its cases, and "lcc2" identifies a load
                                    combination in a way "10" never will -- the
                                    Capacity mode's run list has always shown them. */}
                                {/* Stored steps, then the load combinations computed on
                                    request (their status after the name). */}
                                {stepSlots.map((s) => (
                                    <option
                                        key={s.slot}
                                        value={s.slot}
                                        title={s.makeup ? `${s.name ?? s.label} = ${s.makeup}` : undefined}
                                    >
                                        {s.slot + 1}/{nSteps} · {s.name ?? s.label}
                                        {s.kind === "combination" ? ` (${slotStatus.get(s.slot) ?? "computed on request"})` : ""}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                </div>
            )}
            {activeSlot?.kind === "combination" && (inline ? (
                <span
                    className="shrink-0 max-w-56 truncate text-gray-300"
                    data-testid="fea-case-makeup"
                    title={activeSlot.makeup ? `${activeSlot.name ?? activeSlot.label} = ${activeSlot.makeup}` : undefined}
                >
                    {slotStatus.get(activeSlot.slot) ?? "computed on request"}
                </span>
            ) : (
                <div className="text-xs text-gray-300 min-w-0 break-words" data-testid="fea-case-makeup">
                    Load combination {activeSlot.name ?? activeSlot.label}
                    {activeSlot.makeup ? ` = ${activeSlot.makeup}` : ""}
                    {" · "}
                    {slotStatus.get(activeSlot.slot) ?? "computed on request"}
                </div>
            ))}
            {/* Inline there is no room for the sentence: its first clause, the whole as the tooltip. */}
            {unbakedNote && (inline ? (
                <span className="shrink-0 max-w-56 truncate text-amber-300" data-testid="fea-unbaked-note" title={unbakedNote}>
                    {unbakedNote.split(/[.;:]/)[0]}
                </span>
            ) : (
                <div className="text-xs text-amber-300 min-w-0 break-words" data-testid="fea-unbaked-note">
                    {unbakedNote}
                </div>
            ))}

            {/* Row 2 — Scrub slider + period + scale-factor knobs.
                Visually grouped because they all shape the
                deformation amplitude / animation; transport
                buttons on the next row act *on* this group.
                ``w-full`` + ``flex-1`` on the slider so the row
                fills the same total width as row 1 — the slider
                absorbs whatever space is left after the fixed-width
                period + scale inputs. */}
            <div className={inline ? "flex flex-row items-center gap-x-2 shrink-0" : "flex flex-row flex-wrap items-center gap-x-2 gap-y-1 w-full min-w-0"}>
                {timeHistory ? (
                    // A time history: the slider IS the timeline. Scrub it to any frame;
                    // play moves it. Deformation stays at true scale (x below exaggerates).
                    <div className={inline ? "flex items-center gap-2 w-56 shrink-0" : "flex items-center gap-2 flex-1 min-w-0"}>
                        <input
                            type="range"
                            min={0}
                            max={Math.max(nSteps - 1, 0)}
                            step={1}
                            value={stepIndex}
                            onChange={(e) => onStepChange(parseInt(e.target.value, 10))}
                            className="min-w-0 flex-1 h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30"
                            title="Time"
                            aria-label="Time"
                        />
                        <div
                            className="shrink-0 whitespace-nowrap text-white text-sm font-mono tabular-nums text-right"
                            // Wide enough for the longest time of this history, so the
                            // readout keeps its width while it counts.
                            style={{minWidth: `${timeReadoutChars + 3}ch`}}
                            title="Time of the shown frame"
                        >
                            t = {stepTimeText} s
                        </div>
                        {framePreload && (!framePreload.done || framePreload.total < framePreload.nFrames) && (
                            <div
                                className="shrink-0 whitespace-nowrap text-white/70 text-xs font-mono tabular-nums"
                                title={
                                    framePreload.total < framePreload.nFrames
                                        ? "Frames held in memory for smooth playback. The rest do not fit the memory budget and load as they play."
                                        : "Loading every frame into memory for smooth playback"
                                }
                            >
                                {framePreload.done
                                    ? `${framePreload.total}/${framePreload.nFrames} frames cached`
                                    : `caching ${framePreload.loaded}/${framePreload.total}`}
                            </div>
                        )}
                    </div>
                ) : (
                    <div className={inline ? "flex items-center gap-2 w-40 shrink-0" : "flex items-center gap-2 flex-1 min-w-0"}>
                        <input
                            type="range"
                            min={lo}
                            max={hi}
                            step={factorStep}
                            value={factor}
                            onChange={(e) => onFactorChange(parseFloat(e.target.value))}
                            className="min-w-0 flex-1 h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30"
                        />
                        <div className="shrink-0 text-white text-sm font-mono w-12 text-center">
                            {factor.toFixed(2)}
                        </div>
                    </div>
                )}
            </div>

            {/* Row 3 — on the left the actions (play / stop / data panel / export / options),
                one compact size, packed; on the right how playing is drawn (deformation
                scale, period, beams as solid). The right group wraps under the buttons on a
                narrow panel. */}
            <div className={inline ? "flex flex-row flex-nowrap items-center gap-x-3 shrink-0" : "flex flex-row flex-wrap items-center gap-x-3 gap-y-2 min-w-0"}>
                <span className="flex items-center gap-1.5">
                <button
                    className={TRANSPORT_BUTTON}
                    onClick={isPlaying ? onPause : onPlay}
                    title={
                        timeHistory
                            ? isPlaying ? "Pause" : "Play the time history"
                            : isPlaying ? "Pause oscillation" : "Play oscillation"
                    }
                >
                    <PlayPauseIcon/>
                </button>
                <button
                    className={TRANSPORT_BUTTON}
                    onClick={onStop}
                    title="Stop and reset deformation to 0"
                >
                    <StopIcon/>
                </button>
                <button
                    className={TRANSPORT_BUTTON}
                    onClick={onToggleData}
                    title="Toggle simulation data panel"
                >
                    <FEMDataPanelIcon/>
                </button>
                <AnimationExportButton/>
                {!inline && (
                <button
                    className={`${TRANSPORT_BUTTON} ${showOptions ? TRANSPORT_BUTTON_ON : ""}`}
                    onClick={() => setShowOptions((v) => !v)}
                    title="Visualisation options"
                    aria-pressed={showOptions}
                >
                    <GearIcon/>
                </button>
                )}
                </span>
                {/* How playing is drawn: the deformation scale and the period (the two
                    numbers that shape the animation), and beams as solid. Drawn as the same
                    field as the dropdowns above.

                    Beams as solid is here as well as in the Model tab, through the same
                    store flag + scene helper, so the two can never disagree: in the paradoc
                    embed this panel is the only one there is, and a beam drawn as a line cannot
                    show a torsion mode at all. Only offered when the bundle has beam solids. */}
                <span className={inline ? "flex flex-nowrap items-center gap-x-3 whitespace-nowrap" : "ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-white"}>
                    <label
                        className="flex items-center gap-1"
                        title="Deformation scale: multiplies the displacement drawn (1 = true scale)"
                    >
                        <DeformScaleIcon className="shrink-0 text-blue-300" aria-hidden />
                        <span className="text-gray-300">Scale ×</span>
                        <input
                            type="number"
                            min={0}
                            step={0.1}
                            value={scaleFactor}
                            onChange={(e) => onScaleFactorChange(parseFloat(e.target.value))}
                            className={`${FIELD_CONTROL} w-14 font-mono tabular-nums`}
                            aria-label="Deformation scale"
                        />
                    </label>
                    {/* Only a mode shape has a period worth setting: it oscillates. A static
                        load case plays as a load-up sweep at the default pace, and a time
                        history plays its own time axis -- a "period" means nothing for either. */}
                    {activeField?.analysis_kind === "eigen" && (
                        <label className="flex items-center gap-1" title="Period: seconds per oscillation when playing">
                            <span className="text-gray-300">T</span>
                            <input
                                type="number"
                                min={0.1}
                                step={0.1}
                                value={period}
                                onChange={(e) => setPeriod(parseFloat(e.target.value))}
                                className={`${FIELD_CONTROL} w-12 font-mono tabular-nums`}
                                aria-label="Oscillation period in seconds"
                            />
                            <span className="text-gray-400">s</span>
                        </label>
                    )}
                    {lazyCases && (
                        <label
                            className="flex items-center gap-1"
                            title="Colour scale over every load combination (the envelope) instead of the shown case"
                        >
                            <input
                                type="checkbox"
                                checked={envelopeMode}
                                onChange={(e) => onEnvelopeToggle(e.target.checked)}
                            />
                            <span className="text-gray-300">All combinations</span>
                        </label>
                    )}
                    {showBeamSolids && beamSolidsToggle.offered && (
                        <label
                            className={`flex items-center gap-1${beamSolidsToggle.enabled ? "" : " opacity-50"}`}
                            title={beamSolidsToggle.title}
                        >
                            <input
                                type="checkbox"
                                checked={beamSolidsToggle.enabled && beamSolidsVisible}
                                disabled={!beamSolidsToggle.enabled}
                                onChange={(e) => onToggleBeamSolids(e.target.checked)}
                            />
                            <span className="text-gray-300">Beams as solid</span>
                        </label>
                    )}
                    {showBeamSolids && beamSolidsToggle.offered && beamSolidsToggle.enabled && beamSolidsVisible && (
                        <label className="flex items-center gap-1" title="Element edges on the beam solids">
                            <input
                                type="checkbox"
                                checked={beamSolidEdgesVisible}
                                onChange={(e) => onToggleBeamSolidEdges(e.target.checked)}
                            />
                            <span className="text-gray-300">Solid edges</span>
                        </label>
                    )}
                    {fieldHasResultPointMarkers(activeField) && (
                        <label className="flex items-center gap-1" title="Markers at the result points, coloured by their exact values">
                            <input
                                type="checkbox"
                                checked={resultPointMarkersVisible}
                                onChange={(e) => setFeaResultPointMarkersVisible(e.target.checked)}
                            />
                            <span className="text-gray-300">Result points</span>
                        </label>
                    )}
                </span>
            </div>

            {optionsOpen && (
                <div className={inline ? "flex shrink-0 flex-row flex-nowrap items-center gap-x-3 whitespace-nowrap" : "flex flex-row flex-wrap items-center gap-x-3 gap-y-1 px-2 py-1 rounded-sm bg-gray-900/40 text-xs text-white"}>
                    {showColorToggle && (
                        <label
                            className="flex items-center gap-1"
                            title="Paint the model with the result field, or draw it in its base material"
                        >
                            <input
                                type="checkbox"
                                checked={resultColorsVisible}
                                onChange={(e) => onResultColorsToggle(e.target.checked)}
                            />
                            <span className="text-gray-300">Result colours</span>
                        </label>
                    )}
                    <label className="flex items-center gap-1">
                        <span className="text-gray-300">Colormap</span>
                        <select
                            className="text-black bg-white rounded-sm px-1 py-0.5"
                            value={colormap}
                            onChange={(e) => onColormapChange(e.target.value)}
                        >
                            {COLORMAP_NAMES.map((name) => (
                                <option key={name} value={name}>
                                    {name}
                                </option>
                            ))}
                        </select>
                    </label>
                    <button
                        ref={scaleAnchorRef}
                        type="button"
                        className={
                            "rounded-sm px-1.5 py-0.5 text-white " +
                            (showScale ? "bg-blue-700" : "bg-gray-700 hover:bg-gray-600")
                        }
                        onClick={() => setShowScale((v) => !v)}
                        aria-pressed={showScale}
                        title="Colour scale: range, contour bands"
                    >
                        Scale…
                    </button>
                    {/* Layer + IP reduction — only for element fields
                        (per_type buckets). Nodal fields have no IP /
                        layer axis. Layer dropdown options are the
                        union of layer markers across the field's
                        per_type ip_layouts, plus "all" for no
                        filter. */}
                    {isElemField && layerOptions.length > 1 && (
                        <label className="flex items-center gap-1">
                            <span className="text-gray-300">Surface/layer</span>
                            <select
                                className="text-black bg-white rounded-sm px-1 py-0.5"
                                value={layer}
                                onChange={(e) => onLayerChange(e.target.value)}
                                title={hasExactSurface
                                    ? "Which shell surface to display"
                                    : "Which integration-point layer to read"}
                            >
                                {layerOptions.map((opt) => (
                                    <option key={opt} value={opt}>{opt}</option>
                                ))}
                            </select>
                        </label>
                    )}
                    {showIpReduction && (
                        <label className="flex items-center gap-1">
                            <span className="text-gray-300">IP reduction</span>
                            <select
                                className="text-black bg-white rounded-sm px-1 py-0.5"
                                value={ipReduction}
                                onChange={(e) => onIpReductionChange(e.target.value)}
                                title="How to collapse integration-point values per element"
                            >
                                {ipReductionOptions.map((opt) => (
                                    <option key={opt} value={opt}>{opt}</option>
                                ))}
                            </select>
                        </label>
                    )}
                    {isElemField && (
                        <label
                            className="flex items-center gap-1"
                            title="Average each vertex's element scalars across the elements that touch it. Hides per-element discontinuities."
                        >
                            <input
                                type="checkbox"
                                checked={nodalAverage}
                                onChange={(e) => onNodalAverageToggle(e.target.checked)}
                            />
                            <span className="text-gray-300">Smooth (nodal avg)</span>
                        </label>
                    )}
                    {/* Warp toggle. Disabled (and forced visually off)
                        for reaction-force fields — applying a force
                        vector as a morph would visualise force as
                        displacement, which is semantically wrong.
                        For displacement / stress / strain / other the
                        toggle controls whether the active field
                        (displacement) or the manifest's displacement
                        field (everything else) drives the morph. */}
                    {showWarpToggle && (() => {
                        const isReaction = activeField?.category === "reaction";
                        return (
                            <label
                                className={
                                    "flex items-center gap-1 " +
                                    (isReaction ? "opacity-50 cursor-not-allowed" : "")
                                }
                                title={
                                    isReaction
                                        ? "Reaction-force fields are never warped"
                                        : "Show the deformed shape using the displacement field"
                                }
                            >
                                <input
                                    type="checkbox"
                                    checked={!isReaction && warpEnabled}
                                    disabled={isReaction}
                                    onChange={(e) => onWarpToggle(e.target.checked)}
                                />
                                <span className="text-gray-300">Warp by displacement</span>
                            </label>
                        );
                    })()}
                    {/* The element grid's zoom fade: off keeps the edges at full strength at any
                        distance; the number is how far apart (CSS px) the edges must be on screen
                        to show at full strength. */}
                    <label
                        className="flex items-center gap-1"
                        title="Fade the element edges as they crowd together when zoomed out, so the result colours stay readable"
                    >
                        <input
                            type="checkbox"
                            checked={edgeFade}
                            onChange={(e) => onEdgeFadeChange(e.target.checked, edgeFadeFullPx)}
                        />
                        <span className="text-gray-300">Fade edges when zoomed out</span>
                    </label>
                    {edgeFade && (
                        <label
                            className="flex items-center gap-1"
                            title="Edges show at full strength while they are at least this many pixels apart on screen; they are gone at 3/8 of it. Higher fades sooner."
                        >
                            <span className="text-gray-300">from</span>
                            <input
                                type="number"
                                min={1}
                                max={100}
                                step={1}
                                value={edgeFadeFullPx}
                                onChange={(e) => onEdgeFadeChange(edgeFade, parseFloat(e.target.value))}
                                className={`${FIELD_CONTROL} w-12 font-mono tabular-nums`}
                                aria-label="Edge spacing in pixels below which the edges fade"
                            />
                            <span className="text-gray-400">px</span>
                        </label>
                    )}
                </div>
            )}

            {optionsOpen && showScale && (inline ? (
                <AnchoredPopover anchorRef={scaleAnchorRef} onClose={() => setShowScale(false)} ariaLabel="Colour scale">
                    <ResultScaleSettings onClose={() => setShowScale(false)} />
                </AnchoredPopover>
            ) : (
                <ResultScaleSettings onClose={() => setShowScale(false)} />
            ))}
        </div>
    );
};

// Inline icon — same look as the other transport buttons. Defined
// here rather than as a sibling file because it's the only consumer.
// ``w-6 h-6`` matches PlayPauseIcon / StopIcon so the gear button
// reads at the same visual weight as the rest of the transport row.

const GltfClipControls: React.FC<ControlPanelProps> = ({onToggleData}) => {
    // This viewer instance's scene-graph handles. Aliased to the names the
    // body already uses, which until now were module-level globals.
    const {animationController: animationControllerRef} = useViewerRefs();
    const {selectedAnimation, currentKey, setCurrentKey} = useAnimationStore();
    const roundedCurrentKey = parseFloat(currentKey.toFixed(2));

    const handleAnimationChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const animationName = e.target.value;
        animationControllerRef.current?.setCurrentAnimation(animationName);
    };

    const togglePlayPause = () => {
        animationControllerRef.current?.togglePlayPause();
    };

    const stopAnimation = () => {
        animationControllerRef.current?.stopAnimation();
    };

    const seekAnimation = (time: number) => {
        animationControllerRef.current?.seek(time);
    };

    useEffect(() => {
        if (animationControllerRef.current) {
            setCurrentKey(animationControllerRef.current.getCurrentTime());
        }
    }, [selectedAnimation]);

    return (
        <div className="flex flex-row items-center gap-x-2 min-w-0">
            <select
                className="text-black font-bold py-2 px-4 rounded-sm w-60"
                value={selectedAnimation}
                onChange={handleAnimationChange}
            >
                <option title="No Animation" key="No Animation" value="No Animation">
                    No Animation
                </option>
                {animationControllerRef.current?.getAnimationNames().map((name) => (
                    <option key={name} value={name}>
                        {name}
                    </option>
                ))}
            </select>

            <button
                className={TRANSPORT_BUTTON}
                onClick={togglePlayPause}
            >
                <PlayPauseIcon/>
            </button>
            <button
                className={TRANSPORT_BUTTON}
                onClick={stopAnimation}
            >
                <StopIcon/>
            </button>
            <button
                className={TRANSPORT_BUTTON}
                onClick={onToggleData}
            >
                <FEMDataPanelIcon/>
            </button>
            <div className="flex items-center gap-2 min-w-[100px] max-w-sm w-full">
                <input
                    type="range"
                    min="0"
                    max={animationControllerRef.current?.getDuration() || 0}
                    value={roundedCurrentKey}
                    step={animationControllerRef.current?.getStep() || 0}
                    onChange={(e) => {
                        const newTime = parseFloat(e.target.value);
                        setCurrentKey(newTime);
                        seekAnimation(newTime);
                    }}
                    className="min-w-0 flex-1 h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30"
                />
                <div className="shrink-0 text-white text-sm font-mono w-12 text-center">
                    {roundedCurrentKey}
                </div>
            </div>
        </div>
    );
};

export default SimulationControls;
