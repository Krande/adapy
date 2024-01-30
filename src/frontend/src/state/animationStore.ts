// animationStore.ts
import {create} from 'zustand';
import {AnimationMixer, AnimationAction, AnimationClip} from 'three';
import * as THREE from 'three';
import {colorVerticesBasedOnDeformation} from '../utils/colorize_vector_data';

type State = {
    animations: AnimationClip[];
    setAnimations: (animations: AnimationClip[]) => void;
    selectedAnimation: string;
    animationDuration: number;
    currentKey: number;
    mixer: AnimationMixer | null;
    action: AnimationAction | null;
    setSelectedAnimation: (animation: string) => void;
    setAnimationDuration: (duration: number) => void;
    setCurrentKey: (key: number) => void;
    setMixer: (mixer: AnimationMixer | null) => void;
    setAction: (action: AnimationAction | null) => void;
    playAnimation: (animationName: string) => void;
    pauseAnimation: () => void;
    seekAnimation: (time: number) => void;
    isPlaying: boolean;
    setIsPlaying: (isPlaying: boolean) => void;
};

export const useAnimationStore = create<State>((set) => ({
    animations: [],
    selectedAnimation: '',
    animationDuration: 0,
    currentKey: 0,
    mixer: null,
    action: null,
    isPlaying: false,
    setIsPlaying: (isPlaying) => set({isPlaying}),

    setAnimations: (animations) => {
        set((state) => {
            // Set the animations array
            const newState = {...state, animations};


            // If there are animations, select the first one
            if (animations.length > 0) {
                // If there is no selected animation, select the "No Animation" option
                if (!state.selectedAnimation) {
                    newState.selectedAnimation = 'No Animation';
                } else {
                    const firstAnimationName = animations[0].name;
                    const selectedClip = animations.find(clip => clip.name === firstAnimationName);
                    const selectedDuration = selectedClip ? selectedClip.duration : 0;

                    newState.selectedAnimation = firstAnimationName;
                    newState.animationDuration = selectedDuration;
                }
            }

            return newState;
        });
    },
    setSelectedAnimation: (animation) => {
        set((state) => {
            if (animation === 'No Animation') {
                if (state.action) {
                    state.action.stop();
                }

                // Reset morph target influences
                if (state.mixer) {
                    const root = state.mixer.getRoot();
                    if (root instanceof THREE.Object3D) {
                        root.traverse((object: THREE.Object3D) => {
                            if (object instanceof THREE.Mesh && object.morphTargetInfluences) {
                                object.morphTargetInfluences.fill(0);
                            }
                        });
                    }
                }

                return {
                    ...state,
                    selectedAnimation: animation,
                    animationDuration: 0,
                };
            } else {
                const selectedClip = state.animations.find(clip => clip.name === animation);
                const selectedDuration = selectedClip ? selectedClip.duration : 0;
                const selectedClipIndex = state.animations.findIndex(clip => clip.name === animation);


                // find the morphed mesh object related to selectedClip animation
                if (state.mixer) {
                    const root = state.mixer.getRoot();
                    let morphTargetObject = null;
                    if (root instanceof THREE.Object3D) {
                        root.traverse((object: THREE.Object3D) => {
                            if (object instanceof THREE.Mesh && object.morphTargetInfluences) {
                                morphTargetObject = object;
                            }
                        });
                    }
                    if (morphTargetObject) {
                        colorVerticesBasedOnDeformation(morphTargetObject, selectedClipIndex);
                    }
                }


                return {
                    ...state,
                    selectedAnimation: animation,
                    animationDuration: selectedDuration,
                };
            }
        });
    },
    setAnimationDuration: (duration) => set({animationDuration: duration}),
    setCurrentKey: (key) => set({currentKey: key}),
    setMixer: (mixer) => set({mixer}),
    setAction: (action) => set({action}),
    seekAnimation: (time: number) => {
        set(state => {
            if (state.action) {
                state.action.time = time; // Seek the animation to the specified time
                //state.action.paused = true; // Optional: Pause on seek
            }
            return state;
        });
    },
    playAnimation: (animationName: string) => {
        set(state => {
            if (state.mixer) {
                // Find the clip by the given animation name
                const clip = state.animations.find(clip => clip.name === animationName);

                if (clip) {
                    // Check if the action is already created and if it's paused
                    if (state.action && state.action.getClip().name === animationName) {
                        if (state.action.paused) {
                            state.action.paused = false; // Resume if paused
                            state.action.play();
                        }
                    } else {
                        // If not paused or not the current action, start a new action
                        if (state.action) {
                            state.action.stop();
                        }
                        const newAction = state.mixer.clipAction(clip);
                        newAction.play();
                        return {...state, action: newAction};
                    }
                }
            }
            return state;
        });
    },

    pauseAnimation: () => {
        set(state => {
            if (state.action) {
                state.action.paused = true;
            }
            return state;
        });
    },
}));