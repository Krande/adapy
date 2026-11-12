// What models are on screen, as the loads that put them there: so that switching between the three.js
// viewport and the three.js-free one (CanvasWrapper) can load the same models into the viewport that
// comes up. Neither viewport can hand its models to the other: the three.js one keeps three.js groups,
// the other keeps them inside the WebGPU renderer, and the one being left is torn down.
//
// Every load through `loadModel` is recorded here. A replace starts the list over, an overlay joins it
// (replacing an earlier load of the same source), and a model unloaded from the viewer leaves it (the
// list follows the loaded source names). No three.js here: the switch reads it without the loader.
import {useModelState} from "@/state/modelState";
import type {LoadModelSource} from "./loadModel";

let entries: LoadModelSource[] = [];

/** A load into the viewport on screen finished. */
export function recordModelLoad(source: LoadModelSource): void {
    const {placement = "replace", sourceName} = source;
    // A replay records no admin load metrics.
    const kept: LoadModelSource = {...source, metrics: false};
    if (placement === "replace") entries = [kept];
    else entries = [...entries.filter((e) => !sourceName || e.sourceName !== sourceName), kept];
}

/** The loads that put the models on screen there now, oldest first. */
export function loadedModelJournal(): {entries: readonly LoadModelSource[]} {
    return {entries};
}

/** Forget every load (tests). */
export function clearModelJournal(): void {
    entries = [];
}

// A model unloaded (or the scene cleared) is no longer on screen: its load is dropped. An unnamed
// load cannot be unloaded on its own; it goes when the viewer has no loaded source left.
useModelState.subscribe((s, prev) => {
    if (s.loadedSourceNames === prev.loadedSourceNames || !entries.length) return;
    const names = s.loadedSourceNames;
    entries = names.size === 0 ? [] : entries.filter((e) => !e.sourceName || names.has(e.sourceName));
});

/** Load the models of the list again, into the viewport now on screen: the first replacing what it
 *  shows, the others beside it. Loads one at a time, in the order they were made. */
export async function replayModelJournal(snapshot: readonly LoadModelSource[], isCurrent: () => boolean): Promise<void> {
    const {loadModel} = await import("./loadModel");
    for (let i = 0; i < snapshot.length; i++) {
        if (!isCurrent()) return;
        const source = snapshot[i];
        try {
            // Placed as the first load placed it: an overlay recentres by default, a replace does not.
            const translate = source.translate ?? (source.placement ?? "replace") === "overlay";
            await loadModel({...source, placement: i === 0 ? "replace" : "overlay", translate, metrics: false});
        } catch (err) {
            console.error(`[viewport switch] loading ${source.sourceName || "a model"} into the new viewport failed:`, err);
        }
    }
}
