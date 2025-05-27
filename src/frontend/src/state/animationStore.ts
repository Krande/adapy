import {create} from 'zustand';

// Define the AnimationState type
export type AnimationState = {
    hasAnimation: boolean; // Indicates if the animation controller has any animations
    selectedAnimation: string; // The currently selected animation name
    currentKey: number; // The current key (time) for the animation
    isPlaying: boolean; // Indicates if the animation is currently playing
    isControlsVisible: boolean; // Indicates if the animation controls are visible
    setCurrentKey: (key: number) => void; // Sets the current key (time) for the animation
    setSelectedAnimation: (animationName: string) => void; // Sets the selected animation
    setHasAnimation: (hasAnimation: boolean) => void; // Sets if there are animations loaded
    setIsPlaying: (isPlaying: boolean) => void; // Sets the play/pause state of the animation
    setIsControlsVisible: (isControlsVisible: boolean) => void; // Sets the visibility of the animation controls
};

export const useAnimationStore = create<AnimationState>((set) => ({
    hasAnimation: false, // Initially, there are no animations
    selectedAnimation: '', // No animation selected initially
    currentKey: 0, // Initial key (time) for the animation
    isPlaying: false, // Animation is not playing initially
    isControlsVisible: false, // Animation controls are not visible initially
    setCurrentKey: (key: number) => {
        set({ currentKey: key });
    },

    setSelectedAnimation: (animationName: string) => {
        set({ selectedAnimation: animationName });
    },

    setHasAnimation: (hasAnimation: boolean) => {
        set({ hasAnimation });
    },
    setIsPlaying: (isPlaying: boolean) => {
        set({ isPlaying });
    },
    setIsControlsVisible: (isControlsVisible: boolean) => {
        set({ isControlsVisible });
    },
}));
