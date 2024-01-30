import React, {useState} from 'react';
import {useAnimationStore} from '../../state/animationStore';
import {handleAnimationChange, stopAnimation, togglePlayPause} from "../../utils/animation_utils";

const playpause_svg = <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}
                           stroke="currentColor" className="w-6 h-6">
    <path strokeLinecap="round" strokeLinejoin="round"
          d="M21 7.5V18M15 7.5V18M3 16.811V8.69c0-.864.933-1.406 1.683-.977l7.108 4.061a1.125 1.125 0 0 1 0 1.954l-7.108 4.061A1.125 1.125 0 0 1 3 16.811Z"/>
</svg>

const stop_svg = <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}
                      stroke="currentColor" className="w-6 h-6">
    <path strokeLinecap="round" strokeLinejoin="round"
          d="M5.25 7.5A2.25 2.25 0 0 1 7.5 5.25h9a2.25 2.25 0 0 1 2.25 2.25v9a2.25 2.25 0 0 1-2.25 2.25h-9a2.25 2.25 0 0 1-2.25-2.25v-9Z"/>
</svg>

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
        <div className={"w-full h-full flex flex-row space-x-2"}>
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
                {playpause_svg}
            </button>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={stopAnimation}>{stop_svg}
            </button>


            <div className="m-2 flex flex-row">
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
                <div className={"p-2"}>{roundedCurrentKey}</div>
            </div>


        </div>
    );
};

export default AnimationControls;