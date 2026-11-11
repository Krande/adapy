// What is hidden and selected in one loaded model, read and put back by the viewport that draws it.
//
// The history's entries for models loaded and removed (utils/scene/modelHistory.ts) keep this per
// model, so a model that an undo loads again looks as it did when it was removed. A viewport that
// holds its models itself says how here; the three.js viewport's models are read from its scene
// (utils/scene/sceneViewRestore.ts). No three.js in this module.
import type {RecordedSourceView} from "@/utils/scene/sceneRecord";

export interface SourceViews {
    /** What is hidden and selected in the model loaded from `sourceName`, by object id, or null
     *  when nothing is. Still answers for a model the load in progress has just replaced. */
    capture(sourceName: string): RecordedSourceView | null;
    /** Hide and select again what `view` names in the model loaded from `sourceName`, once it is
     *  on screen. */
    apply(sourceName: string, view: RecordedSourceView): Promise<void>;
}

let own: SourceViews | null = null;

/** The views of the viewport on screen, when it keeps them itself; null on the three.js viewport. */
export function viewportSourceViews(): SourceViews | null {
    return own;
}

/** Make `views` the ones asked. Returns the function that withdraws them. */
export function setViewportSourceViews(views: SourceViews): () => void {
    own = views;
    return () => {
        if (own === views) own = null;
    };
}
