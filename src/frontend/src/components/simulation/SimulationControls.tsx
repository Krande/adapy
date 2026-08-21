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

import React, {useEffect, useMemo, useState} from "react";
import {buttonClasses, fieldClasses} from "@/components/ui";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useTableNavStore} from "@/state/tableNavStore";
import {animationControllerRef} from "@/state/refs";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {COLORMAP_NAMES} from "@/utils/scene/fea/colormaps";
import {resetFeaAnimationPhase} from "@/utils/scene/fea/feaAnimationDriver";
import {load_fea_streaming} from "@/utils/scene/handlers/load_fea_streaming";
import {followerUrl} from "@/utils/simChannel";
import PlayPauseIcon from "../icons/PlayPauseIcon";
import StopIcon from "../icons/StopIcon";
import SimulationDataInfoPanel from "./SimulationDataInfoPanel";
import FEMDataPanelIcon from "../icons/FEMDataPanelIcon";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {useViewerStores} from "@/state/AdaViewerContext";
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

export interface SimulationControlsProps {
    // The follower / new-window entry boots the frame already maximized as a
    // full window and locks it to a single plugin tab (no tab strip, no
    // animation tab). Omitted for the normal docked panel.
    initialMode?: SimFrameMode;
    forcedTabId?: string;
}

const SimulationControls: React.FC<SimulationControlsProps> = ({initialMode = "docked", forcedTabId}) => {
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    // Panel-visibility now lives in tableNavStore so external
    // triggers (ObjectInfoBoxComponent's "Show in data" button) can
    // open the panel without prop-drilling. Local state would still
    // work for the button-toggle case but breaks the cross-component
    // open-from-info-panel flow.
    const showSimData = useTableNavStore((s) => s.isPanelOpen);
    const togglePanel = useTableNavStore((s) => s.togglePanel);

    const stores = useViewerStores();
    const [mode, setMode] = useState<SimFrameMode>(initialMode);
    const [activeTabState, setActiveTabState] = useState<string>("animation");
    const activeTab = forcedTabId ?? activeTabState;

    // Plugin-contributed Simulation tabs (fem-sidebar panels carrying `asTab`).
    // Rebuilt each render so activation + badges track live store state.
    const baseCtx = makePluginContext("", stores);
    const tabs = getSimulationTabs(baseCtx);

    // Snap back to Animation if the active plugin tab goes away (model unloaded).
    useEffect(() => {
        if (forcedTabId) return;
        if (activeTabState !== "animation" && !tabs.some((t) => t.panel.id === activeTabState)) {
            setActiveTabState("animation");
        }
    }, [tabs, activeTabState, forcedTabId]);

    const onOpenWindow = () => {
        const panelId = forcedTabId ?? (activeTab !== "animation" ? activeTab : tabs[0]?.panel.id ?? "");
        const source = useFeaAnimationStore.getState().sourceName || "model";
        const scope = scopeUrlPart(useScopeStore.getState().current);
        window.open(followerUrl(source, panelId, scope), "_blank", "noopener,width=920,height=820");
    };

    // Forced-tab hosts (new-window follower / maximized window) are canvas-less
    // and load only result sidecars, so the plugin's activation predicate can be
    // false and `tabs` empty even though the panel's data is present. Fall back to
    // an activation-ignoring lookup by id so the follower mounts the real plugin
    // controls instead of silently dropping to the Animation tab.
    const activePluginTab =
        tabs.find((t) => t.panel.id === activeTab) ??
        (forcedTabId ? findSimulationTabById(forcedTabId) : null);

    const body = (
        <div className="flex flex-col gap-2 min-w-0">
            {!forcedTabId && tabs.length > 0 && (
                <SimTabStrip
                    tabs={tabs}
                    activeTab={activeTab}
                    onSelect={setActiveTabState}
                    ctxFor={(pid) => makePluginContext(pid, stores)}
                />
            )}
            {activePluginTab ? (
                <PluginTabBody
                    pluginId={activePluginTab.pluginId}
                    panel={activePluginTab.panel}
                    ctx={makePluginContext(activePluginTab.pluginId, stores)}
                />
            ) : (
                <AnimationTab
                    sessionActive={sessionActive}
                    showSimData={showSimData}
                    onToggleData={togglePanel}
                />
            )}
        </div>
    );

    return (
        <SimWindowFrame mode={mode} setMode={setMode} onOpenWindow={onOpenWindow}>
            {body}
        </SimWindowFrame>
    );
};

/**
 * The built-in animation controls, without a panel around them.
 *
 * Exported so the Results toolbar can host them: the Simulation panel is no longer part
 * of a stock Results layout, and these are the only things in it that are not
 * plugin-contributed.
 *
 * Which set you get follows the SESSION, not the mode. An FEA result and a GLTF clip are
 * different animations with genuinely different knobs, and picking between them here
 * rather than at each call site is what stops the toolbar and the panel disagreeing about
 * which one is playing.
 */
export const AnimationControls: React.FC = () => {
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    // Data-panel toggling belongs to the toolbar, and has since the transport moved.
    const noop = () => {};
    return sessionActive ? (
        <FeaModeControls showSimData={false} onToggleData={noop} />
    ) : (
        <GltfClipControls showSimData={false} onToggleData={noop} />
    );
};

// Built-in "animation" tab — the FEA / GLTF transport controls, the optional
// data panel, plus the additive plugin color-field picker and any buttonless
// fem-sidebar panels (asTab panels are hoisted to their own tabs instead).
const AnimationTab: React.FC<{sessionActive: boolean; showSimData: boolean; onToggleData: () => void}> = ({
    sessionActive,
    showSimData,
    onToggleData,
}) => (
    <div className="flex flex-col gap-2">
        {sessionActive ? (
            <FeaModeControls showSimData={showSimData} onToggleData={onToggleData} />
        ) : (
            <GltfClipControls showSimData={showSimData} onToggleData={onToggleData} />
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
    activeTab: string;
    onSelect: (id: string) => void;
    ctxFor: (pluginId: string) => AdaPluginContext;
}> = ({tabs, activeTab, onSelect, ctxFor}) => (
    <div
        className="flex flex-wrap gap-0.5 border-b border-white/15"
        role="tablist"
        aria-label="Simulation panel section"
    >
        <SimTabButton id="animation" label="Animation" active={activeTab === "animation"} onClick={() => onSelect("animation")} />
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
                    ? "border-accent text-[var(--ada-panel-text)]"
                    : "border-transparent text-content-muted pointer-fine:hover:text-[var(--ada-panel-text)]")
            }
        >
            {contextual && <span className="w-1.5 h-1.5 rounded-full bg-info" aria-hidden="true" />}
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
                <div className="rounded-md border border-fail bg-surface-0 p-2 text-xs text-content">
                    <div className="font-semibold text-fail">Plugin “{pluginId}” hit an error</div>
                    <div className="mt-0.5 mb-1.5 break-words text-content-muted">{error.message}</div>
                    <button
                        type="button"
                        onClick={reset}
                        className="rounded-sm bg-surface-2 px-2 py-1 text-white pointer-fine:hover:bg-surface-3"
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

const FeaModeControls: React.FC<ControlPanelProps> = ({onToggleData}) => {
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
        setLayer,
        setIpReduction,
        setNodalAverage,
    } = useFeaAnimationStore();

    // Options panel toggle — currently houses just the colormap
    // picker. Kept folded by default so the controls row stays
    // single-line; new per-session preferences (background tone,
    // scalar-bar tick density, etc.) land in here without adding
    // top-level buttons.
    const [showOptions, setShowOptions] = useState(false);

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

    const reductionOptions = useMemo<string[]>(() => {
        if (!activeField) return [];
        const out: string[] = [];
        if (activeField.kind.startsWith("vector")) out.push("magnitude");
        for (const c of activeField.components) out.push(c);
        return out;
    }, [activeField]);

    // Element-field path: expose Layer / IP reduction pickers when the
    // active field has per_type buckets. ``layerOptions`` is the union
    // of ``ip_layout[*].layer`` across all buckets, plus an "all"
    // sentinel for "no layer filter". Buckets without ip_layout don't
    // contribute options; the kernel falls back to "all" for them.
    const isElemField = !!(activeField?.per_type && activeField.per_type.length > 0);
    const layerOptions = useMemo<string[]>(() => {
        if (!isElemField) return [];
        const layers = new Set<string>();
        for (const bk of activeField!.per_type!) {
            for (const l of bk.ip_layout ?? []) {
                if (l.layer) layers.add(l.layer);
            }
        }
        const out = Array.from(layers).sort();
        out.push("all");
        return out;
    }, [activeField, isElemField]);
    const ipReductionOptions = ["max_abs", "max", "min", "mean"];

    // Composite morph influence = slider factor × user-set scale.
    // Captured in a helper so every load_fea_streaming call site
    // computes it the same way; missing one would leave a stale
    // unscaled morph after a field / reduction / step change.
    const morphInfluence = factor * scaleFactor;

    const onFieldChange = (newFieldName: string) => {
        if (!sourceName || !manifest) return;
        const newField = manifest.fields.find(
            (f) => f.name_canonical === newFieldName,
        );
        if (!newField) return;
        // Snap reduction to the new field's default — the prior
        // reduction (e.g. "DZ") may not exist on a different field
        // and would silently break colouring.
        const newReduction = newField.default_view?.reduction ?? "magnitude";
        // Element fields carry default ``layer`` + ``ip_reduction`` in
        // default_view. Switching from nodal → element with a stored
        // ``layer`` that doesn't exist on this bucket (e.g. "top" on a
        // solid-only field) would silently fall through to "all" via
        // layerIpIndices; snapping to the bake's recommended default
        // gives a more predictable first frame and keeps the dropdown
        // value in sync with what the kernel actually used.
        if (newField.per_type && newField.per_type.length > 0) {
            if (newField.default_view?.layer) setLayer(newField.default_view.layer);
            if (newField.default_view?.ip_reduction) setIpReduction(newField.default_view.ip_reduction);
        }
        // Step 0 too — step counts differ between fields, and a
        // stepIndex from the prior field would leave the slider out
        // of bounds on the new one.
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName: newFieldName,
            stepIndex: 0,
            reduction: newReduction,
            displacementScale: morphInfluence,
        });
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

    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onStop = () => {
        setIsPlaying(false);
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
        setLayer(next);
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

    return (
        <div className="flex flex-col gap-2 min-w-0">
            {/* Row 1 — Field / Comp / Step selectors only. Gear
                moved down to the transport row so this stays a
                focused "what are you looking at" line.
                Mobile width: ``flex-1`` on each label distributes
                the available width evenly between the three
                dropdowns, ``min-w-0 truncate`` on the selects
                lets long field names (e.g. "Contact Normal Force
                Vector") ellipsise instead of pushing the row past
                the viewport. ``sm:flex-none`` reverts to natural
                width on desktop so the dropdowns size to content
                with ``justify-between`` spacing the groups. */}
            {manifest && (
                <div className="flex flex-row items-center justify-between gap-x-2 w-full min-w-0 text-xs text-white">
                    <label className="flex items-center gap-1 min-w-0 flex-1 sm:flex-none">
                        <span className="text-content shrink-0">Field</span>
                        <select
                            className={`${fieldClasses("sm")} min-w-0 flex-1 sm:flex-none truncate`}
                            value={fieldName ?? ""}
                            onChange={(e) => onFieldChange(e.target.value)}
                        >
                            {/* Both nodal (AFBL blob) and element
                                (AFEL per_type) fields are renderable;
                                the picker doesn't filter — branching
                                happens in load_fea_streaming based on
                                ``field.per_type``. */}
                            {manifest.fields.map((f) => (
                                <option key={f.name_canonical} value={f.name_canonical}>
                                    {f.name_canonical}
                                </option>
                            ))}
                        </select>
                    </label>
                    {reductionOptions.length > 0 && (
                        <label className="flex items-center gap-1 min-w-0 flex-1 sm:flex-none">
                            <span className="text-content shrink-0">Comp</span>
                            <select
                                className={`${fieldClasses("sm")} min-w-0 flex-1 sm:flex-none truncate`}
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
                        <label className="flex items-center gap-1 min-w-0 flex-1 sm:flex-none">
                            <span className="text-content shrink-0">Step</span>
                            <select
                                className={`${fieldClasses("sm")} min-w-0 flex-1 sm:flex-none sm:max-w-40 truncate`}
                                value={stepIndex}
                                disabled={nSteps <= 1}
                                onChange={(e) => onStepChange(parseInt(e.target.value, 10))}
                                title={`Step ${stepIndex + 1} of ${nSteps}`}
                            >
                                {activeField.steps.map((s) => (
                                    <option key={s.i} value={s.i}>
                                        {s.i + 1}/{nSteps} · {s.label}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                </div>
            )}

            {/* Row 2 — Scrub slider + period + scale-factor knobs.
                Visually grouped because they all shape the
                deformation amplitude / animation; transport
                buttons on the next row act *on* this group.
                ``w-full`` + ``flex-1`` on the slider so the row
                fills the same total width as row 1 — the slider
                absorbs whatever space is left after the fixed-width
                period + scale inputs. */}
            <div className="flex flex-row items-center gap-x-2 w-full min-w-0">
                <div className="flex items-center gap-2 flex-1 min-w-[100px]">
                    <input
                        type="range"
                        min={lo}
                        max={hi}
                        step={factorStep}
                        value={factor}
                        onChange={(e) => onFactorChange(parseFloat(e.target.value))}
                        className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-accent-subtle"
                    />
                    <div className="text-white text-sm font-mono w-12 text-center">
                        {factor.toFixed(2)}
                    </div>
                </div>
                <div className="text-white text-xs flex items-center gap-1">
                    T
                    <input
                        type="number"
                        min={0.1}
                        step={0.1}
                        value={period}
                        onChange={(e) => setPeriod(parseFloat(e.target.value))}
                        className={`${fieldClasses("sm")} w-16`}
                        title="Oscillation period (seconds)"
                    />
                    s
                </div>
                {/* Warp-scale knob: multiplier on top of the
                    [-1..1] / [0..1] sweep, exaggerates the
                    morph delta. Default 1. */}
                <div className="text-white text-xs flex items-center gap-1">
                    ×
                    <input
                        type="number"
                        min={0}
                        step={0.1}
                        value={scaleFactor}
                        onChange={(e) => onScaleFactorChange(parseFloat(e.target.value))}
                        className={`${fieldClasses("sm")} w-16`}
                        title="Warp scale factor — multiplier on top of the slider value (default 1)"
                    />
                </div>
            </div>

            {/* Row 3 — the visualisation-options toggle.

                Play, Stop and the data-panel toggle used to sit here too. They are in
                the Results mode toolbar now: the panel keeps the controls that pick a
                VALUE (field, step, colormap, deform scale) and the toolbar takes the
                ones that DO something. Having both meant two play buttons for one
                playback state, differently drawn and differently placed.

                The gear stays, because what it reveals is this panel's own options row
                — a disclosure for the panel, not an action on the scene. */}
            <div className="flex flex-row items-center gap-x-2 min-w-0">
                <button
                    className={
                        buttonClasses("secondary", "sm") +
                        (showOptions ? " ring-2 ring-accent" : "")
                    }
                    onClick={() => setShowOptions((v) => !v)}
                    title="Visualisation options"
                    aria-pressed={showOptions}
                >
                    <GearIcon/>
                </button>
            </div>

            {showOptions && (
                <div className="flex flex-row items-center gap-x-3 px-2 py-1 rounded-sm bg-surface-0 text-xs text-white">
                    <label className="flex items-center gap-1">
                        <span className="text-content">Colormap</span>
                        <select
                            className={fieldClasses("sm")}
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
                    {/* Layer + IP reduction — only for element fields
                        (per_type buckets). Nodal fields have no IP /
                        layer axis. Layer dropdown options are the
                        union of layer markers across the field's
                        per_type ip_layouts, plus "all" for no
                        filter. */}
                    {isElemField && layerOptions.length > 0 && (
                        <label className="flex items-center gap-1">
                            <span className="text-content">Layer</span>
                            <select
                                className={fieldClasses("sm")}
                                value={layer}
                                onChange={(e) => onLayerChange(e.target.value)}
                                title="Which integration-point layer to read"
                            >
                                {layerOptions.map((opt) => (
                                    <option key={opt} value={opt}>{opt}</option>
                                ))}
                            </select>
                        </label>
                    )}
                    {isElemField && (
                        <label className="flex items-center gap-1">
                            <span className="text-content">IP reduction</span>
                            <select
                                className={fieldClasses("sm")}
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
                            <span className="text-content">Smooth (nodal avg)</span>
                        </label>
                    )}
                    {/* Beam-solid toggle moved to the Scene > FEM panel
                        (single source of truth — FemConceptsPanel). */}
                    {/* Warp toggle. Disabled (and forced visually off)
                        for reaction-force fields — applying a force
                        vector as a morph would visualise force as
                        displacement, which is semantically wrong.
                        For displacement / stress / strain / other the
                        toggle controls whether the active field
                        (displacement) or the manifest's displacement
                        field (everything else) drives the morph. */}
                    {(() => {
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
                                <span className="text-content">Warp by displacement</span>
                            </label>
                        );
                    })()}
                </div>
            )}
        </div>
    );
};

// Inline icon — same look as the other transport buttons. Defined
// here rather than as a sibling file because it's the only consumer.
// ``w-6 h-6`` matches PlayPauseIcon / StopIcon so the gear button
// reads at the same visual weight as the rest of the transport row.
const GearIcon: React.FC = () => (
    <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-6 h-6"
        aria-hidden="true"
    >
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
);

const GltfClipControls: React.FC<ControlPanelProps> = () => {
    const {selectedAnimation, currentKey, setCurrentKey} = useAnimationStore();
    const roundedCurrentKey = parseFloat(currentKey.toFixed(2));

    const handleAnimationChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const animationName = e.target.value;
        animationControllerRef.current?.setCurrentAnimation(animationName);
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
        <div className="flex w-full flex-col gap-2 min-w-0 text-xs text-white">
            <label className="flex items-center gap-2 min-w-0">
            <span className="shrink-0 text-content">Clip</span>
            <select
                // Was a fixed w-60 with no min-w-0: in a narrow dock the row ran past
                // the panel and the clip name clipped mid-word, which is what made
                // "No Animation" unreadable.
                className={`${fieldClasses("sm")} min-w-0 flex-1 truncate`}
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
            </label>

            <div className="flex w-full items-center gap-2 min-w-0">
                <span className="shrink-0 text-content">Time</span>
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
                    className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-accent-subtle"
                />
                <div className="text-white text-sm font-mono w-12 text-center">
                    {roundedCurrentKey}
                </div>
            </div>
        </div>
    );
};

export default SimulationControls;
