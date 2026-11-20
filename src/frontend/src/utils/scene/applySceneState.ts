/**
 * Put a scene state back (`./sceneRecord` `SceneRecord`): a saved state, or one a producer built --
 * a detail model's "Load" builds one from the clash check it came from.
 *
 * ORDER, each step on what the previous one left:
 *   1. the whole models, through the loads a page reload repeats (skipping what is in the scene);
 *   2. the members, as a saved group's "Load into scene" loads and selects them;
 *   3. what was hidden and selected;
 *   4. the clash check: its target, its result read back by key, its markers;
 *   5. the active detail model: its GLB overlaid, its produced joints listed;
 *   6. the camera, last, so no model's own fit moves it afterwards.
 * A step that fails leaves a note and the rest still runs: half a scene is more use than none.
 *
 * Pure orchestration: what each step does to the viewer comes through `SceneStateDeps`
 * (`./sceneStateDeps` has the live ones).
 */

import type {ClashResult} from "@/state/clashCheck/result";
import {recordedSourceName, type RecordedCamera, type RecordedClash, type RecordedDetail, type RecordedModel, type RecordedView, type SceneRecord} from "./sceneRecord";
import type {GroupMember} from "@/utils/groups/savedGroups";

export interface SceneStateDeps {
    /** Scene names of the models loaded now. */
    loaded(): ReadonlySet<string>;
    /** Load one model; true when it is in the scene afterwards. */
    loadModel(scope: string, m: RecordedModel): Promise<boolean>;
    /** Load and select members; returns what could not be loaded, in sentences. */
    loadMembers(scope: string, name: string, members: readonly GroupMember[]): Promise<readonly string[]>;
    /** Hide and select again; the one note about what was not found, or null. */
    applyView(view: RecordedView): Promise<string | null>;
    /** Put the check on the Clashes tab with its markers. `result`: already read, so not again. */
    restoreClash(scope: string, clash: RecordedClash, result?: ClashResult): Promise<void>;
    /** Overlay the detail model and list its joints, as the active one. */
    openDetail(scope: string, detail: RecordedDetail): Promise<void>;
    applyCamera(camera: RecordedCamera): void;
}

export interface ApplyOptions {
    /** The name members are loaded under (the group's, or what they are the inputs of). */
    membersName?: string;
    /** The clash result, when the producer read it already. */
    clashResult?: ClashResult;
}

export interface ApplyOutcome {
    /** Models that could not be loaded, by scene name. */
    failed: string[];
    /** What could not be put back, in sentences. */
    notes: string[];
    /** Why the detail model could not be loaded, when it could not. Not in `notes`: a caller loading
     *  a detail model reports it as its own failure. */
    detailError: string | null;
}

const why = (e: unknown) => (e instanceof Error ? e.message : String(e));

export async function applySceneState(
    deps: SceneStateDeps,
    scope: string,
    state: SceneRecord,
    opts: ApplyOptions = {},
): Promise<ApplyOutcome> {
    const failed: string[] = [];
    const notes: string[] = [];
    let detailError: string | null = null;

    for (const m of state.models) {
        const name = recordedSourceName(m);
        if (deps.loaded().has(name)) continue;
        let ok = false;
        try {
            ok = await deps.loadModel(scope, m);
        } catch {
            ok = false;
        }
        if (!ok) failed.push(name);
    }
    if (failed.length) notes.push(`${failed.length} model${failed.length === 1 ? "" : "s"} could not be loaded: ${failed.join(", ")}`);

    if (state.members?.length) {
        try {
            notes.push(...(await deps.loadMembers(scope, opts.membersName ?? "scene state", state.members)));
        } catch (e) {
            notes.push(`The elements could not be loaded: ${why(e)}`);
        }
    }

    if (state.view) {
        try {
            const note = await deps.applyView(state.view);
            if (note) notes.push(note);
        } catch (e) {
            notes.push(`The hidden and selected objects could not be restored: ${why(e)}`);
        }
    }

    if (state.clash) {
        try {
            await deps.restoreClash(scope, state.clash, opts.clashResult);
        } catch (e) {
            notes.push(`The clash check could not be restored: ${why(e)}`);
        }
    }

    if (state.detail) {
        try {
            await deps.openDetail(scope, state.detail);
        } catch (e) {
            detailError = why(e);
        }
    }

    if (state.camera) {
        try {
            deps.applyCamera(state.camera);
        } catch {
            // The camera is a convenience.
        }
    }
    return {failed, notes, detailError};
}
