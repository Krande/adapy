import React from 'react';
import {useAnimationStore} from '../../state/animationStore';
import {handleAnimationChange, stopAnimation, togglePlayPause} from "../../utils/scene/animations/animation_utils";
import PlayPauseIcon from "../icons/PlayPauseIcon";
import StopIcon from "../icons/StopIcon";


const AnimationControls = () => {
    const {
        animations,
        selectedAnimation,
        animationDuration,
        currentKey,
        setCurrentKey,
        seekAnimation,
    } = useAnimationStore();


    const roundedCurrentKey = parseFloat(currentKey.toFixed(2));


    return (
        <div className={"w-full h-full flex flex-row"}>
            <select
                className={"text-black font-bold py-2 px-4 ml-2 rounded w-60"}
                value={selectedAnimation}
                onChange={handleAnimationChange}
            >
                <option title={"No Animation"} key={"No Animation"} value={"No Animation"}>No Animation</option>
                {animations.map(animation => (
                    // On hover, show the full string of the animation
                    <option title={animation.name} key={animation.name} value={animation.name}>{animation.name}</option>
                ))}
            </select>

            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={togglePlayPause}
            >
                <PlayPauseIcon/>
            </button>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={stopAnimation}>
                <StopIcon/>
            </button>


            <div className="p-2 flex flex-row">
                <input
                    type="range"
                    min="0"
                    max={animationDuration}
                    value={roundedCurrentKey}
                    step={animationDuration / 100}
                    onChange={(e) => {
                        const newTime = parseFloat(e.target.value);
                        setCurrentKey(newTime);
                        seekAnimation(newTime); // Use the method from the store
                    }}
                />
                <div className={"px-2 w-12 text-center"}>{roundedCurrentKey}</div>
            </div>


        </div>
    );
};

export default AnimationControls;