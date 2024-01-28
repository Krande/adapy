import React, {useState} from 'react';
import {useAnimationStore} from '../../state/animationStore';

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

    const [isPlaying, setIsPlaying] = useState(false); // Add this line

    const roundedCurrentKey = parseFloat(currentKey.toFixed(2));
    const handleAnimationChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const animationName = e.target.value;
        setSelectedAnimation(animationName);
        // stop animation if it's playing
        if (isPlaying) {
            stopAnimation();
        }
    };

    const stopAnimation = () => {
        pauseAnimation();
        seekAnimation(0);
        setIsPlaying(false); // Add this line
    }

    const togglePlayPause = () => { // Modify this function
        if (isPlaying) {
            pauseAnimation();
        } else {
            playAnimation(selectedAnimation);
        }
        setIsPlaying(!isPlaying);
    }

    return (
        <div className={"w-full h-full flex flex-col space-y-4"}>
            <select
                className={"text-black font-bold py-2 px-4 ml-2 rounded"}
                value={selectedAnimation}
                onChange={handleAnimationChange}
            >
                <option>No Animation</option>
                {animations.map(animation => (
                    <option key={animation.name} value={animation.name}>{animation.name}</option>
                ))}
            </select>

            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={togglePlayPause}
            >
                Play/Pause
            </button>
            <button
                className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                onClick={stopAnimation}>Stop
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