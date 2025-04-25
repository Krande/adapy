import React from "react";
import { useAnimationStore } from "../../state/animationStore";
import {
  handleAnimationChange,
  stopAnimation,
  togglePlayPause,
} from "../../utils/scene/animations/animation_utils";
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
    <div className={"flex flex-row items-center gap-2 min-w-0"}>
      <select
        className={"text-black font-bold py-2 px-4 rounded w-60"}
        value={selectedAnimation}
        onChange={handleAnimationChange}
      >
        <option
          title={"No Animation"}
          key={"No Animation"}
          value={"No Animation"}
        >
          No Animation
        </option>
        {animations.map((animation) => (
          // On hover, show the full string of the animation
          <option
            title={animation.name}
            key={animation.name}
            value={animation.name}
          >
            {animation.name}
          </option>
        ))}
      </select>

      <button
        className={
          "bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
        }
        onClick={togglePlayPause}
      >
        <PlayPauseIcon />
      </button>
      <button
        className={
          "bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
        }
        onClick={stopAnimation}
      >
        <StopIcon />
      </button>

      <div className="flex items-center gap-2 min-w-[200px] max-w-sm w-full">
        <input
          type="range"
          min="0"
          max={animationDuration}
          value={roundedCurrentKey}
          step={animationDuration / 100}
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

export default AnimationControls;
