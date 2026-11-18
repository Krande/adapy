// The FEA result controls: field, component and step pickers, the transport, the warp and
// period knobs, and the options (colormap, layer, IP reduction, warp, edge fade, scale).

import React, { useRef, useState, useSyncExternalStore } from "react";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { edgeFadeSnapshot, subscribeEdgeFade, useFeaControlActions } from "@/utils/scene/fea/feaControls";
import { isMirroredWindow } from "@/layout/sceneActions";
import { COLORMAP_NAMES } from "@/utils/scene/fea/colormaps";
import ResultScaleSettings from "../ResultScaleSettings";
import AnchoredPopover from "../../common/AnchoredPopover";
import { fieldHasResultPointMarkers } from "@/utils/scene/fea/resultPointMarkers";
import { beamSolidsToggleState } from "@/utils/scene/fea/streaming/beamSolidsToggle";
import GearIcon from "../../icons/GearIcon";
import PlayPauseIcon from "../../icons/PlayPauseIcon";
import StopIcon from "../../icons/StopIcon";
import FEMDataPanelIcon from "../../icons/FEMDataPanelIcon";
import AnimationExportButton from "../AnimationExportButton";
import { IS_EMBED_BUILD } from "@/runtime/embedBuild";
import { FIELD_CONTROL, TRANSPORT_BUTTON, TRANSPORT_BUTTON_ON } from "../controlStyles";
import DeformScaleIcon from "../../icons/DeformScaleIcon";
import type { ControlPanelProps, ResultControlsOptions } from "./types";
import { useFeaFieldView } from "./useFeaFieldView";

export const FeaModeControls: React.FC<ControlPanelProps & {display?: ResultControlsOptions}> = ({onToggleData, display = {}}) => {
    const {layout = "panel", showWarpToggle = true, showBeamSolids = true, showColorToggle = false} = display;
    const inline = layout === "inline";
    const {
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
        beamSolidsVisible,
        beamSolidsUnavailable,
        beamSolidEdgesVisible,
        resultPointMarkersVisible,
        timeHistory,
        envelopeMode,
        framePreload,
        resultColorsVisible,
    } = useFeaAnimationStore();
    // What the controls do: on this window's scene, or sent to the main window from a pop-out.
    const actions = useFeaControlActions();
    // The window of a pop-out has no canvas to export from.
    const mirrored = isMirroredWindow();
    const {on: edgeFade, fullPx: edgeFadeFullPx} = useSyncExternalStore(subscribeEdgeFade, edgeFadeSnapshot, edgeFadeSnapshot);
    const onEdgeFadeChange = (on: boolean, fullPx: number) => actions.setEdgeFade({on, fullPx});
    const beamSolidsToggle = beamSolidsToggleState(manifest, beamSolidsUnavailable);
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

    // What the field, component and step pickers offer for the shown field (`./useFeaFieldView`).
    const {
        activeField,
        fieldPickerValue,
        activeUnit,
        stepTimeText,
        timeReadoutChars,
        reductionOptions,
        fieldHierarchy,
        propertyFields,
        lazyCases,
        stepSlots,
        slotStatus,
        activeSlot,
        unbakedNote,
        isElemField,
        layerOptions,
        hasExactSurface,
        showIpReduction,
        ipReductionOptions,
    } = useFeaFieldView({manifest, fieldName, reduction, stepIndex, timeHistory, sourceName});

    // The controls' actions (the transport's, the loader's, the pickers'): `useFeaControlActions`,
    // run here or sent to the main window. Core's playback toolbar calls the same ones.
    const onFieldChange = actions.selectField;
    const onReductionChange = actions.setReduction;
    const onPlay = actions.play;
    const onPause = actions.pause;
    const onStop = actions.stop;
    // Manual factor drag: written through to the morph influence at once. The RAF driver
    // only runs while playing, so a direct write is the hook for paused scrubs.
    const onFactorChange = actions.setFactor;
    // A NaN from an in-progress edit (the user clearing the field) is ignored.
    const onScaleFactorChange = actions.setScale;
    const onStepChange = actions.setStep;
    // Warp, layer, IP reduction, the envelope, smoothing and the colormap re-run the loader on
    // the shown (field, step, reduction): the field blob is cached, so it is CPU work only.
    const onWarpToggle = actions.setWarp;
    const onLayerChange = actions.setLayer;
    const onIpReductionChange = actions.setIpReduction;
    const onEnvelopeToggle = actions.setEnvelope;
    const onNodalAverageToggle = actions.setNodalAverage;
    const onColormapChange = actions.setColormap;
    const onResultColorsToggle = actions.setResultColors;
    const onToggleBeamSolidEdges = actions.setBeamSolidEdges;
    const onToggleBeamSolids = actions.setBeamSolids;

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
                {/* The embed has no MP4 encoder (vite.config.embed.ts stubs it out). */}
                {!mirrored && !IS_EMBED_BUILD && <AnimationExportButton/>}
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
                                onChange={(e) => actions.setPeriod(parseFloat(e.target.value))}
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
                                onChange={(e) => actions.setResultPointMarkers(e.target.checked)}
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
