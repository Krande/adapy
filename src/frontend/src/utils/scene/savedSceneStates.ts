// Saved scene states: what the scene showed (`./sceneRecord`), named and kept per scope so anyone in
// it can put it back. One document per scope, `_scenes/states.json` (`@/services/savedSceneStates`),
// kept the way the saved groups are: read the latest, apply one change, write it back.
//
// Pure: the document, its validation and its changes. Node-testable.

import {sceneRecordDoc, sceneRecordFrom, type SceneRecord} from "./sceneRecord";

export const SCENE_STATES_SCHEMA = "ada.viewer/scene-states@1";
/** Where a scope's document is kept: a hidden scope-state prefix the server lets the viewer write. */
export const SCENE_STATES_BLOB_KEY = "_scenes/states.json";

export interface SavedSceneState {
    readonly id: string;
    readonly name: string;
    readonly created_at: string;
    readonly created_by?: string;
    readonly state: SceneRecord;
}

export interface SceneStatesDoc {
    readonly schema: typeof SCENE_STATES_SCHEMA;
    readonly states: readonly SavedSceneState[];
}

export const EMPTY_SCENE_STATES_DOC: SceneStatesDoc = {schema: SCENE_STATES_SCHEMA, states: []};

export class SceneStatesDocError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "SceneStatesDocError";
    }
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const nonEmpty = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0;

/** The document in `raw` (text or parsed). Nothing stored is an empty document; a document of
 *  another schema THROWS, so a change is never written over one this viewer cannot read. A state
 *  that is not the known shape is dropped. */
export function parseSceneStatesDoc(raw: unknown): SceneStatesDoc {
    let value: unknown = raw;
    if (typeof raw === "string") {
        if (!raw.trim()) return EMPTY_SCENE_STATES_DOC;
        try {
            value = JSON.parse(raw);
        } catch {
            throw new SceneStatesDocError("the saved scene states document is not JSON");
        }
    }
    if (value === null || value === undefined) return EMPTY_SCENE_STATES_DOC;
    if (!isRecord(value)) throw new SceneStatesDocError("the saved scene states document is not a JSON object");
    if (value.schema !== SCENE_STATES_SCHEMA) {
        throw new SceneStatesDocError(
            `the saved scene states document is ${JSON.stringify(value.schema)}; this viewer reads ${SCENE_STATES_SCHEMA} only`,
        );
    }
    const states: SavedSceneState[] = [];
    for (const s of Array.isArray(value.states) ? value.states : []) {
        if (!isRecord(s) || !nonEmpty(s.id) || !nonEmpty(s.name) || typeof s.created_at !== "string") continue;
        const state = sceneRecordFrom(s.state);
        if (!state) continue;
        states.push({
            id: s.id,
            name: s.name,
            created_at: s.created_at,
            ...(nonEmpty(s.created_by) ? {created_by: s.created_by} : {}),
            state,
        });
    }
    return {schema: SCENE_STATES_SCHEMA, states};
}

export function serialiseSceneStatesDoc(doc: SceneStatesDoc): string {
    return JSON.stringify({
        schema: SCENE_STATES_SCHEMA,
        states: doc.states.map((s) => ({...s, state: sceneRecordDoc(s.state)})),
    });
}

export type SceneStatesChange =
    | {readonly kind: "put"; readonly state: SavedSceneState}
    | {readonly kind: "delete"; readonly id: string};

/** `doc` with one change applied; every other state -- others' saved since this viewer read -- kept. */
export function applySceneStatesChange(doc: SceneStatesDoc, change: SceneStatesChange): SceneStatesDoc {
    if (change.kind === "put") {
        const states = [...doc.states];
        const i = states.findIndex((s) => s.id === change.state.id);
        if (i < 0) states.push(change.state);
        else states[i] = change.state;
        return {schema: SCENE_STATES_SCHEMA, states};
    }
    return {schema: SCENE_STATES_SCHEMA, states: doc.states.filter((s) => s.id !== change.id)};
}

/** A new state id: fixed-width time first, so ids sort in the order they were saved. */
export function newSceneStateId(now: number = Date.now(), random: () => number = Math.random): string {
    const r = Math.floor(random() * 36 ** 6)
        .toString(36)
        .padStart(6, "0");
    return `s${now.toString(36).padStart(9, "0")}${r}`;
}

/** One line for a saved state: what it holds. */
export function sceneStateSummary(s: SceneRecord): string {
    const parts: string[] = [];
    const n = (k: number, one: string) => `${k} ${one}${k === 1 ? "" : "s"}`;
    if (s.models.length) parts.push(n(s.models.length, "model"));
    if (s.members?.length) parts.push(n(s.members.length, "element"));
    if (s.clash) parts.push("clash check");
    if (s.detail) parts.push("detail model");
    if (s.camera) parts.push("camera");
    return parts.join(" · ") || "empty";
}
