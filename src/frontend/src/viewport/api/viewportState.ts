// A viewport's undoable state, as the undo / redo history (utils/scene/viewerHistory.ts) sees it: what
// is selected and what is hidden. The history does not know how a viewport keeps either. It asks the
// viewport on screen for its state before an action, asks what the action changed, and hands one of
// the two ends of that step back when the step is undone or redone. No three.js here.

/** Which parts of its state a viewport is asked to note. An action that cannot hide or show anything
 *  (a selection, a section plane, a selection box) leaves `visibility` out, and the viewport then
 *  does not look at what is hidden at all. */
export interface ViewportStateParts {
    readonly selection?: boolean;
    readonly visibility?: boolean;
}

/** One end of a step: the viewport's state before an action, or after it. Only the viewport that
 *  made it reads it. */
export interface ViewportState {
    /** How many objects are selected in it. */
    readonly selected: number;
}

/** What an action changed in a viewport, measured against the state captured before it. */
export interface ViewportStateChange {
    /** Whether the selection is another one now. */
    readonly selection: boolean;
    /** Whether anything was hidden or shown. */
    readonly visibility: boolean;
    /** How many objects the action hid, and how many it showed. */
    readonly hidden: number;
    readonly shown: number;
    /** The state the action left, for `restoreState`. Made when asked for: an action that changed
     *  nothing needs none. */
    after(): ViewportState;
}

export interface ViewportUndoState {
    /** Note the state an action is about to change. */
    captureState(what: ViewportStateParts): ViewportState;
    /** What has changed since `before` was captured. */
    stateChange(before: ViewportState): ViewportStateChange;
    /** Go to one end of a step: `before` (an undo) or the state its change gave as `after` (a redo).
     *  The selection becomes the one that end had. What is hidden moves by the step's difference
     *  only, so objects the step did not touch stay as they are now. Objects that are no longer on
     *  screen are left out, and a state another viewport made is ignored. */
    restoreState(state: ViewportState): void;
}
