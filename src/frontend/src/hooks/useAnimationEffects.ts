// useAnimationEffects.ts
import { useEffect } from 'react';
import * as THREE from 'three';
import { useAnimationStore } from '../state/animationStore';

export const useAnimationEffects = (animations: THREE.AnimationClip[], scene: THREE.Scene) => {
    const { setMixer, setAnimations } = useAnimationStore();

    useEffect(() => {
        setAnimations(animations);
        const newMixer = new THREE.AnimationMixer(scene);
        setMixer(newMixer);

        return () => {
            setMixer(null);
        };
    }, [animations, scene, setAnimations, setMixer]);
};