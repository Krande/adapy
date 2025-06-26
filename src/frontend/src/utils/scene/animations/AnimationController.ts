// AnimationController.ts
import * as THREE from 'three';
import {colorVerticesBasedOnDeformation} from "../analysis/colorize_vector_data";
import {sceneRef} from "../../../state/refs";
import {AnimationState, useAnimationStore} from "../../../state/animationStore";

export class AnimationController {
    private mixer: THREE.AnimationMixer;
    private actions: Map<string, THREE.AnimationAction> = new Map();
    public currentAction: THREE.AnimationAction | null = null;
    public meshMap: Map<string, string[]> = new Map();
    public readonly animation_store: AnimationState | null = null;

    constructor(scene: THREE.Scene) {
        this.mixer = new THREE.AnimationMixer(scene);
        this.animation_store = useAnimationStore.getState();
    }

    // clear all animation data
    public clear(): void {
        this.mixer.stopAllAction();
        this.actions.clear();
        this.currentAction = null;
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

    private _get_mesh_from_action(action: THREE.AnimationAction): THREE.Mesh | null {
        const clip = action.getClip();
        const node_names = this.meshMap.get(clip.name);
        const scene = sceneRef.current;

        if (node_names && scene) {
            for (const node_name of node_names) {
                const searchName = node_name.replace('.', '');

                const mesh = scene.getObjectByName(searchName) as THREE.Mesh;

                if (mesh) {
                    return mesh;
                }
            }
        }
        return null;
    }

    public setCurrentAnimation(clipName: string): void {
        if (clipName === "No Animation") {
            this.animation_store?.setSelectedAnimation(clipName);
            this.stopAnimation()

            // use the default material and the first clip action to find the associated mesh
            const defaultMaterial = new THREE.MeshStandardMaterial({color: 0x808080, side: THREE.DoubleSide});
            const clip1 = this.actions.values().next().value
            if (clip1) {
                const mesh = this._get_mesh_from_action(clip1);
                if (mesh) {
                    mesh.material = defaultMaterial;  // Reset to default material
                    mesh.geometry.attributes.position.needsUpdate = true;  // Update geometry
                }
            }
            return;
        }
        const action = this.actions.get(clipName);
        if (action) {
            if (this.currentAction) {
                this.currentAction.stop();
            }
            this.currentAction = action;
            useAnimationStore.getState().setSelectedAnimation(clipName);

            // get the index of the clip
            const mesh = this._get_mesh_from_action(action);
            const index = this.getActionIndex()
            if (mesh) {
                colorVerticesBasedOnDeformation(mesh, index);  // Colorize vertices based on deformation
            }
            this.animation_store?.setIsPlaying(false);
            this.currentAction.paused = true;
        }
    }

    // Play/pause the current animation
    public togglePlayPause(): void {
        if (this.currentAction) {
            this.animation_store?.setIsPlaying(!this.animation_store?.isPlaying);
            this.currentAction.paused = !this.currentAction.paused;
            if (!this.currentAction.paused) {
                this.currentAction.play();
            }
        }
    }

    // Stop the current animation (and reset to the beginning)
    public stopAnimation(): void {
        if (this.currentAction) {
            this.currentAction.paused = true;
            this.seek(0);  // Reset to the beginning

            // Clear morph influences to restore shape
            this.animation_store?.setCurrentKey(this.getCurrentTime());  // Update the current key (time) in the store
            this.animation_store?.setIsPlaying(false);  // Set isPlaying to false
        }
    }

    // Seek to a specific time in the current animation
    public seek(time: number): void {
        if (this.currentAction) {
            if (this.currentAction.paused) {
                this.currentAction.play();
            }
            this.currentAction.time = time;
        }
    }

    // Update the animation mixer with delta time
    public update(deltaTime: number): void {
        this.mixer.update(deltaTime);
        if (this.animation_store && this.currentAction?.isRunning()) {
            this.animation_store.setCurrentKey(this.getCurrentTime());  // Update the current key (time) in the store
        }
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

    public getStep(): number {
        if (this.currentAction) {
            return this.getDuration() / 100;
        }
        return 0;
    }

    private getActionIndex(): number {
        if (this.currentAction) {
            const clip = this.currentAction.getClip();
            const index = Array.from(this.actions.keys()).indexOf(clip.name);
            return index;
        }
        return -1;
    }
}
