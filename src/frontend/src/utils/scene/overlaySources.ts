// The overlay manager (viewport/api/overlayManager.ts) told which models are loaded, so an overlay
// drawn over a model goes when that model leaves the scene, by whichever route. Imported for its
// effect by every module that registers an overlay with a `model` lifetime: the model store imports
// three.js, which the manager may not.

import {useModelState} from "@/state/modelState";
import {followLoadedSources} from "@/viewport/api/overlayManager";

followLoadedSources((changed) =>
    useModelState.subscribe((s, prev) => {
        if (s.loadedSourceNames !== prev.loadedSourceNames) changed(s.loadedSourceNames);
    }),
);
