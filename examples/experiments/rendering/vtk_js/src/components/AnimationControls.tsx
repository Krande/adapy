import React from 'react';
import {useAnimationStore} from '../state/animationStore';


const AnimationControls = () => {

    const {
        animations,
        selectedAnimation,
        setSelectedAnimation,
        playAnimation,
        pauseAnimation,
        animationDuration,
        currentKey,
        setCurrentKey,
        seekAnimation,
    } = useAnimationStore();

    const roundedCurrentKey = parseFloat(currentKey.toFixed(2));

    return (
        <div className={"flex flex-col space-y-4"}>
            <select
                className={"font-bold py-2 px-4 ml-2 rounded"}
                value={selectedAnimation}
                onChange={(e) => setSelectedAnimation(e.target.value)}
            >
                {animations.map(animation => (
                    <option key={animation.name} value={animation.name}>{animation.name}</option>
                ))}
            </select>

            <button className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                    onClick={() => playAnimation(selectedAnimation)}>Play
            </button>
            <button className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                    onClick={pauseAnimation}>Pause
            </button>
            <button className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                    onClick={() => seekAnimation(0)}>Reset
            </button>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={() => console.log(useAnimationStore.getState())}
            >Print State
            </button>
            <div className="px-4">
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
            </div>

            <div>Current Key: {roundedCurrentKey}</div>
        </div>
    );
};

export default AnimationControls;
