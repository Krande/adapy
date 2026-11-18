// The transport for a glTF model's own animation clips.

import React, { useEffect } from "react";
import { useAnimationStore } from "@/state/animationStore";
import PlayPauseIcon from "../../icons/PlayPauseIcon";
import StopIcon from "../../icons/StopIcon";
import FEMDataPanelIcon from "../../icons/FEMDataPanelIcon";
import { TRANSPORT_BUTTON } from "../controlStyles";
import { useViewerRefs } from "@/state/AdaViewerContext";
import type { ControlPanelProps } from "./types";

// Inline icon — same look as the other transport buttons. Defined
// here rather than as a sibling file because it's the only consumer.
// ``w-6 h-6`` matches PlayPauseIcon / StopIcon so the gear button
// reads at the same visual weight as the rest of the transport row.

export const GltfClipControls: React.FC<ControlPanelProps> = ({onToggleData}) => {
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
