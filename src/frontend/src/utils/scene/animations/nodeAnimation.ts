// The node animation of loaded GLBs (parts that move, turn and scale over time) on the three.js
// viewport, shown a moment at a time: what `ViewportApi.modelAnimation()` hands the playback clock.
//
// Each model has a mixer of its own, rooted at the model, so its tracks find its own nodes whatever
// another model calls its. A moment is shown by setting each action's time and updating the mixer
// by zero seconds: the clock decides what time it is, the mixer only evaluates the keyframes.
//
// Beside AnimationController, which plays one chosen clip for the legacy clip controls: a clip's
// morph-weight tracks are that path's and are left out here.
import * as THREE from "three";

import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {notifyModelAnimationChange, type ModelAnimation} from "@/viewport/api/viewportApi";

interface AnimatedModel {
    root: THREE.Object3D;
    mixer: THREE.AnimationMixer;
    actions: THREE.AnimationAction[];
    duration: number;
}

const models: AnimatedModel[] = [];
let combined: ModelAnimation | null = null;
let unwatch: (() => void) | null = null;

const movesNodes = (track: THREE.KeyframeTrack): boolean => !/\.morphTargetInfluences(\[|$)/.test(track.name);

function inScene(object: THREE.Object3D): boolean {
    let top = object;
    while (top.parent) top = top.parent;
    return (top as THREE.Scene).isScene === true;
}

function showTime(t: number): void {
    for (let m = 0; m < models.length; m++) {
        const {actions, mixer} = models[m];
        for (let a = 0; a < actions.length; a++) {
            const duration = actions[a].getClip().duration;
            actions[a].time = t < duration ? t : duration;
        }
        mixer.update(0);
    }
    requestRender();
}

function rebuild(): void {
    let duration = 0;
    for (const model of models) duration = Math.max(duration, model.duration);
    combined = models.length ? {duration, showTime} : null;
    if (models.length && !unwatch) {
        // A model leaves the scene by several roads (a replace, an unload, a clear), and each of
        // them changes the model state: that is when to look.
        unwatch = useModelState.subscribe(prune);
    } else if (!models.length && unwatch) {
        unwatch();
        unwatch = null;
    }
    notifyModelAnimationChange();
}

/** Forget the animations of models that are no longer in the scene. */
function prune(): void {
    let removed = false;
    for (let i = models.length - 1; i >= 0; i--) {
        if (inScene(models[i].root)) continue;
        models[i].mixer.stopAllAction();
        models[i].mixer.uncacheRoot(models[i].root);
        models.splice(i, 1);
        removed = true;
    }
    if (removed) rebuild();
}

/** Take up the node animation of a model that was just added to the scene: `clips` as the GLB
 *  loader read them, `root` the loaded scene they name the nodes of. False when none of the clips
 *  moves a node. */
export function registerNodeAnimation(root: THREE.Object3D, clips: readonly THREE.AnimationClip[]): boolean {
    const mixer = new THREE.AnimationMixer(root);
    const actions: THREE.AnimationAction[] = [];
    let duration = 0;
    for (const clip of clips) {
        const tracks = clip.tracks.filter(movesNodes);
        if (!tracks.length) continue;
        // The clip's length is that of its node tracks (-1: measured from them).
        const moving = tracks.length === clip.tracks.length ? clip : new THREE.AnimationClip(clip.name, -1, tracks);
        const action = mixer.clipAction(moving);
        action.play();
        actions.push(action);
        duration = Math.max(duration, moving.duration);
    }
    if (!actions.length) return false;
    models.push({root, mixer, actions, duration});
    rebuild();
    return true;
}

/** The node animation of the models in the scene; null when none has one. */
export function sceneNodeAnimation(): ModelAnimation | null {
    return combined;
}
