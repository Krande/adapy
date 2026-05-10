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
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {animationControllerRef} from "@/state/refs";
import {COLORMAP_NAMES} from "@/utils/scene/fea/colormaps";
import {resetFeaAnimationPhase} from "@/utils/scene/fea/feaAnimationDriver";
import {load_fea_streaming} from "@/utils/scene/handlers/load_fea_streaming";
import PlayPauseIcon from "../icons/PlayPauseIcon";
import StopIcon from "../icons/StopIcon";
import SimulationDataInfoPanel from "./SimulationDataInfoPanel";
import FEMDataPanelIcon from "../icons/FEMDataPanelIcon";

const SimulationControls = () => {
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const [showSimData, setShowSimData] = useState(false);

    return (
        <div className="flex flex-col gap-2">
            {sessionActive ? (
                <FeaModeControls
                    showSimData={showSimData}
                    onToggleData={() => setShowSimData((v) => !v)}
                />
            ) : (
                <GltfClipControls
                    showSimData={showSimData}
                    onToggleData={() => setShowSimData((v) => !v)}
                />
            )}
            {showSimData && (
                <div className="flex-1 overflow-hidden">
                    <SimulationDataInfoPanel/>
                </div>
            )}
        </div>
    );
};

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
        applyStep,
        setFactor,
        setPeriod,
        setIsPlaying,
        setStepIndex,
        setColormap,
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
        // Step 0 too — step counts differ between fields, and a
        // stepIndex from the prior field would leave the slider out
        // of bounds on the new one.
        void load_fea_streaming({
            sourceName,
            manifest,
            fieldName: newFieldName,
            stepIndex: 0,
            reduction: newReduction,
            displacementScale: factor,
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
            displacementScale: factor,
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
    // direct write here is the right hook for paused scrubs.
    const onFactorChange = (newFactor: number) => {
        setFactor(newFactor);
        if (mesh && mesh.morphTargetInfluences) {
            mesh.morphTargetInfluences[0] = newFactor;
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
            displacementScale: factor,
            colormap: next,
        });
    };

    return (
        <div className="flex flex-col gap-2 min-w-0">
            {/* Field + reduction selectors — moved here from the
                retired FeaStreamingPickerModal. Field changes snap
                stepIndex to 0 + reduction to the field's default
                view (different fields can have different step counts
                and component sets). */}
            {manifest && (
                <div className="flex flex-row items-center gap-x-2 min-w-0 text-xs text-white">
                    <label className="flex items-center gap-1">
                        <span className="text-gray-300">Field</span>
                        <select
                            className="text-black bg-white rounded px-1 py-0.5"
                            value={fieldName ?? ""}
                            onChange={(e) => onFieldChange(e.target.value)}
                        >
                            {manifest.fields.map((f) => (
                                <option key={f.name_canonical} value={f.name_canonical}>
                                    {f.name_canonical}
                                </option>
                            ))}
                        </select>
                    </label>
                    {reductionOptions.length > 0 && (
                        <label className="flex items-center gap-1">
                            <span className="text-gray-300">Comp</span>
                            <select
                                className="text-black bg-white rounded px-1 py-0.5"
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
                </div>
            )}

            {/* Step / mode slider. Discrete; updates trigger applyStep
                which re-runs load_fea_streaming with the new step. */}
            <div className="flex flex-row items-center gap-x-2 min-w-0">
                <div className="text-white text-xs w-24 shrink-0">
                    Step {stepIndex + 1} / {nSteps}
                </div>
                <input
                    type="range"
                    min={0}
                    max={Math.max(nSteps - 1, 0)}
                    step={1}
                    value={stepIndex}
                    disabled={nSteps <= 1}
                    onChange={(e) => onStepChange(parseInt(e.target.value, 10))}
                    className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30"
                />
            </div>

            {/* Deformation-scale slider + transport buttons. */}
            <div className="flex flex-row items-center gap-x-2 min-w-0">
                <button
                    className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                    onClick={isPlaying ? onPause : onPlay}
                    title={isPlaying ? "Pause oscillation" : "Play oscillation"}
                >
                    <PlayPauseIcon/>
                </button>
                <button
                    className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                    onClick={onStop}
                    title="Stop and reset deformation to 0"
                >
                    <StopIcon/>
                </button>
                <button
                    className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                    onClick={onToggleData}
                    title="Toggle simulation data panel"
                >
                    <FEMDataPanelIcon/>
                </button>
                <button
                    className={
                        "bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded " +
                        (showOptions ? "ring-2 ring-blue-300" : "")
                    }
                    onClick={() => setShowOptions((v) => !v)}
                    title="Visualisation options"
                    aria-pressed={showOptions}
                >
                    <GearIcon/>
                </button>
                <div className="flex items-center gap-2 min-w-[100px] max-w-sm w-full">
                    <input
                        type="range"
                        min={lo}
                        max={hi}
                        step={factorStep}
                        value={factor}
                        onChange={(e) => onFactorChange(parseFloat(e.target.value))}
                        className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30"
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
                        className="text-black w-16 px-1 rounded"
                        title="Oscillation period (seconds)"
                    />
                    s
                </div>
            </div>

            {showOptions && (
                <div className="flex flex-row items-center gap-x-3 px-2 py-1 rounded bg-gray-900/40 text-xs text-white">
                    <label className="flex items-center gap-1">
                        <span className="text-gray-300">Colormap</span>
                        <select
                            className="text-black bg-white rounded px-1 py-0.5"
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
                </div>
            )}
        </div>
    );
};

// Inline icon — same look as the other transport buttons. Defined
// here rather than as a sibling file because it's the only consumer.
const GearIcon: React.FC = () => (
    <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="w-4 h-4"
        aria-hidden="true"
    >
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
);

const GltfClipControls: React.FC<ControlPanelProps> = ({onToggleData}) => {
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
                className="text-black font-bold py-2 px-4 rounded w-60"
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
                className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                onClick={togglePlayPause}
            >
                <PlayPauseIcon/>
            </button>
            <button
                className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                onClick={stopAnimation}
            >
                <StopIcon/>
            </button>
            <button
                className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
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
                    className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-blue-700 bg-blue-700/30"
                />
                <div className="text-white text-sm font-mono w-12 text-center">
                    {roundedCurrentKey}
                </div>
            </div>
        </div>
    );
};

export default SimulationControls;
