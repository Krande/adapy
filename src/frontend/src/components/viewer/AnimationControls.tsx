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
        <div className={""}>
            <select
                className={""}
                value={selectedAnimation}
                onChange={handleAnimationChange}
            >
                <option>No Animation</option>
                {animations.map(animation => (
                    <option key={animation.name} value={animation.name}>{animation.name}</option>
                ))}
            </select>

            <button
                className={""}
                onClick={togglePlayPause}
            >
                Play/Pause
            </button>
            <button className={""}
                    onClick={stopAnimation}>Stop
            </button>
            <button
                className={""}
                onClick={() => console.log(useAnimationStore.getState())}
            >Print State
            </button>

            <div className="">
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