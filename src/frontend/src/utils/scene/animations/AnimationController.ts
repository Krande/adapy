// AnimationController.ts
import * as THREE from 'three';
import {colorVerticesBasedOnDeformation} from "../analysis/colorize_vector_data";
import {sceneRef} from "../../../state/refs";
import {useAnimationStore, AnimationState} from "../../../state/animationStore";

export class AnimationController {
    private mixer: THREE.AnimationMixer;
    private actions: Map<string, THREE.AnimationAction> = new Map();
    public currentAction: THREE.AnimationAction | null = null;
    public meshMap: Map<string, string[]> = new Map();
    private animation_store: AnimationState | null = null;

    constructor(scene: THREE.Scene) {
        this.mixer = new THREE.AnimationMixer(scene);
        this.animation_store = useAnimationStore.getState();
    }

    // Add an animation clip to the controller
    public addAnimation(clip: THREE.AnimationClip): void {
        const action = this.mixer.clipAction(clip);
        this.actions.set(clip.name, action);
    }

    public setMeshMap(meshMap: Map<string, string[]>): void {
        this.meshMap = meshMap;
    }

    public getAnimationNames(): string[] {
        return Array.from(this.actions.keys());
    }

    public setCurrentAnimation(clipName: string): void {
        const action = this.actions.get(clipName);
        if (action) {
            if (this.currentAction) {
                this.currentAction.stop();
            }

            // get the index of the clip
            const clip = action.getClip();
            const node_names = this.meshMap.get(clip.name);
            const index = 0;
            const scene = sceneRef.current;
            if (node_names && scene) {
                for (const node_name of node_names) {
                    const mesh = scene.getObjectByName(node_name.replace('.', '')) as THREE.Mesh;
                    if (mesh) {
                        colorVerticesBasedOnDeformation(mesh, index);  // Colorize vertices based on deformation
                    }
                }
            }

            action.reset().play();
            this.currentAction = action;
        }
    }

    // Play a specific animation by name
    public playAnimation(clipName: string): void {
        // Stop the current action if there is one
        if (this.currentAction) {
            this.currentAction.stop();
        }

        const action = this.actions.get(clipName);
        if (action) {

            action.reset().play();  // Reset the action and play it
            this.currentAction = action;  // Update the current action
        }
    }

    // Pause the currently playing animation
    public pauseAnimation(): void {
        if (this.currentAction) {
            this.currentAction.paused = true;
        }
    }

    // Resume the currently paused animation
    public resumeAnimation(): void {
        if (this.currentAction) {
            this.currentAction.paused = false;
        }
    }

    // Stop the current animation (and reset to the beginning)
    public stopAnimation(): void {
        if (this.currentAction) {
            this.currentAction.stop();
            this.currentAction = null;
        }
    }

    // Seek to a specific time in the current animation
    public seek(time: number): void {
        if (this.currentAction) {
            this.currentAction.time = time;
        }
    }

    // Update the animation mixer with delta time
    public update(deltaTime: number): void {
        this.mixer.update(deltaTime);
        if (this.animation_store)
            this.animation_store.setCurrentKey(this.getCurrentTime());  // Update the current key (time) in the store
    }

    // Get the current animation time
    public getCurrentTime(): number {
        if (this.currentAction) {
            return this.currentAction.time;
        }
        return 0;
    }

    // Get the duration of the current animation
    public getDuration(): number {
        if (this.currentAction) {
            return this.currentAction.getClip().duration;
        }
        return 0;
    }
}
