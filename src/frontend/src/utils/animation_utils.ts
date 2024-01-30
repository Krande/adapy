import React from "react";
import {useAnimationStore} from "../state/animationStore";

export function handleAnimationChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const animationName = e.target.value;
    useAnimationStore.getState().setSelectedAnimation(animationName);
    // stop animation if it's playing
    stopAnimation();
}

export function stopAnimation() {
    useAnimationStore.getState().pauseAnimation();
    useAnimationStore.getState().seekAnimation(0);
    useAnimationStore.getState().setIsPlaying(false); // Add this line
}

export function togglePlayPause() {
    if (useAnimationStore.getState().isPlaying) {
        useAnimationStore.getState().pauseAnimation();
    } else {
        useAnimationStore.getState().playAnimation(useAnimationStore.getState().selectedAnimation);
    }
    useAnimationStore.getState().setIsPlaying(!useAnimationStore.getState().isPlaying);
}