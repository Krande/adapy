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

import React, {useEffect, useState} from "react";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {animationControllerRef} from "@/state/refs";
import {resetFeaAnimationPhase} from "@/utils/scene/fea/feaAnimationDriver";
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
        applyStep,
        setFactor,
        setPeriod,
        setIsPlaying,
        setStepIndex,
    } = useFeaAnimationStore();

    const [lo, hi] = range;
    // Step granularity for the factor slider — 200 stops over the
    // active range is well below human-visible jumps.
    const factorStep = Math.max((hi - lo) / 200, 0.001);

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

    return (
        <div className="flex flex-col gap-2 min-w-0">
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
        </div>
    );
};

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
