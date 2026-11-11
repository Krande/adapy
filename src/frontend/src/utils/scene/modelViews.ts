/**
 * What a model looked like when it left the scene (its hidden and selected elements), for the
 * history's entries of models loaded and removed (./modelHistory.ts): read from the three.js scene
 * (./sceneViewRestore.ts), or asked of a viewport that holds its models itself
 * (viewport/api/sourceViews.ts).
 */

import type * as THREE from "three";

import {loadedSourceGroups} from "@/state/modelState";
import {viewportSourceViews} from "@/viewport/api/sourceViews";

import type {ModelHistoryDeps} from "./modelHistory";
import {applyRecordedView, captureSourceView} from "./sceneViewRestore";

/** Ms to retry, after a model is loaded again, for meshes a streamed load adds late. */
const REAPPLY_RETRY_MS = [500, 2000];

export const modelViews: Pick<ModelHistoryDeps, "hold" | "captureView" | "applyView"> = {
    hold: (name) => loadedSourceGroups.get(name),
    captureView: (name, held) => {
        const own = viewportSourceViews();
        if (own) return own.capture(name);
        return held ? captureSourceView(held as THREE.Object3D) : null;
    },
    applyView: async (name, view) => {
        const own = viewportSourceViews();
        if (own) return own.apply(name, view);
        await applyRecordedView({v: 1, sources: {[name]: view}}, REAPPLY_RETRY_MS, {replaceSelection: false, resetHistory: false});
    },
};
