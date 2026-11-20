// Where saved scene states live: one blob per scope, `_scenes/states.json`
// (`@/utils/scene/savedSceneStates`) -- shared with everyone in the scope, as the saved groups are
// (`./savedGroups`, whose reasons hold here too). The server keeps `_scenes/` out of file listings.
//
// WITHOUT A SERVER the document is kept in this browser's localStorage, per scope name, best effort.

import {runtime} from "@/runtime/config";
import {ApiError, type ScopeUrl} from "@/services/api/client";
import {filesApi} from "@/services/api/files";
import {
    applySceneStatesChange,
    EMPTY_SCENE_STATES_DOC,
    parseSceneStatesDoc,
    SCENE_STATES_BLOB_KEY,
    serialiseSceneStatesDoc,
    type SceneStatesChange,
    type SceneStatesDoc,
} from "@/utils/scene/savedSceneStates";

const LOCAL_KEY_PREFIX = "ada.viewer.sceneStates:";

/** The stored document. Nothing stored yet is an empty one; any OTHER failure throws, so a change
 *  is never written over a document that could not be read. */
export async function readSceneStatesDoc(scope: string): Promise<SceneStatesDoc> {
    if (!runtime.isRestMode()) {
        let text: string | null = null;
        try {
            text = window.localStorage.getItem(LOCAL_KEY_PREFIX + scope);
        } catch {
            text = null;
        }
        return text ? parseSceneStatesDoc(text) : EMPTY_SCENE_STATES_DOC;
    }
    let bytes: ArrayBuffer;
    try {
        bytes = await filesApi.getBlob(scope as ScopeUrl, SCENE_STATES_BLOB_KEY);
    } catch (e) {
        if (e instanceof ApiError && e.status === 404) return EMPTY_SCENE_STATES_DOC;
        throw e;
    }
    return parseSceneStatesDoc(new TextDecoder().decode(bytes));
}

async function writeSceneStatesDoc(scope: string, doc: SceneStatesDoc): Promise<void> {
    const text = serialiseSceneStatesDoc(doc);
    if (!runtime.isRestMode()) {
        try {
            window.localStorage.setItem(LOCAL_KEY_PREFIX + scope, text);
        } catch (e) {
            throw new Error(`this browser would not store the scene states: ${e instanceof Error ? e.message : String(e)}`);
        }
        return;
    }
    await filesApi.putBlob(scope as ScopeUrl, SCENE_STATES_BLOB_KEY, new Blob([text], {type: "application/json"}));
}

/** Apply one change to the LATEST stored document and write it back; returns what was written. */
export async function changeSceneStates(scope: string, change: SceneStatesChange): Promise<SceneStatesDoc> {
    const next = applySceneStatesChange(await readSceneStatesDoc(scope), change);
    await writeSceneStatesDoc(scope, next);
    return next;
}
