// AnimationControls.tsx
import React, {useEffect, useState} from "react";
import {useAnimationStore} from "../../state/animationStore";
import {animationControllerRef} from "../../state/refs";
import PlayPauseIcon from "../icons/PlayPauseIcon";
import StopIcon from "../icons/StopIcon";
import SimulationDataInfoPanel from "./SimulationDataInfoPanel";
import InfoIcon from "../icons/InfoIcon";
import FEMDataPanelIcon from "../icons/FEMDataPanelIcon";

const SimulationControls = () => {
    const {selectedAnimation, currentKey, setCurrentKey} = useAnimationStore();
    const roundedCurrentKey = parseFloat(currentKey.toFixed(2));
    const [showSimData, setShowSimData] = useState(true);

    // Handle animation change
    const handleAnimationChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const animationName = e.target.value;
        // Play the selected animation using the controller
        animationControllerRef.current?.setCurrentAnimation(animationName);
    };

    const togglePlayPause = () => {
        if (animationControllerRef.current) {
            animationControllerRef.current.togglePlayPause()
        }
    };

    const stopAnimation = () => {
        if (animationControllerRef.current) {
            animationControllerRef.current.stopAnimation();
        }
    };

    const seekAnimation = (time: number) => {
        if (animationControllerRef.current) {
            animationControllerRef.current.seek(time);
        }
    };

    useEffect(() => {
        // Update the current key (time) for the slider based on the animation controller's time
        if (animationControllerRef.current) {
            setCurrentKey(animationControllerRef.current.getCurrentTime());
        }
    }, [selectedAnimation]);

    return (
        <div className="flex flex-col gap-2">
            <div className="flex flex-row items-center gap-x-2 min-w-0 pointer-events-auto">
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
                {/* Toggle button for simulation data */}
                <button
                    className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                    onClick={() => setShowSimData((v) => !v)}
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
            {showSimData && (
                <div className="flex-1 overflow-hidden">
                    <SimulationDataInfoPanel/>
                </div>
            )}
        </div>
    );
};

export default SimulationControls;
