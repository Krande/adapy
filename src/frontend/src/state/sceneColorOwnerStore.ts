// Who owns the scene colouring, as a stack of explicit owners.
//
// The bottom of the stack is always `results`: the user's own result view. A
// plugin mode that declares `ownsSceneColor` is pushed on top of it while
// active and popped when left. Each entry carries the view its owner last had
// on screen (the FEA field selection and the shared legend) and whether that
// owner has painted through core since it took the colouring.
//
// Every paint and every loader landing is tagged with the owner that asked for
// it, and the tag - not a diff of the stores after the fact - decides whose
// view it is:
//
//   - tagged by the owner on top:   its own painting; it keeps the colouring
//   - tagged by anyone else:        set aside under the owner on top; the
//                                   tagged owner's view is updated to it
//
// This store is pure bookkeeping. It never touches the FEA or legend stores
// and imports nothing from three or React, so it runs under `node --test`;
// the adapter in `@/utils/scene/fea/modeSceneColor` snapshots the screen,
// hands the snapshot in, and applies whatever comes back (restore a view, or
// suspend the colouring).

import { create } from "zustand";

/** The id of the owner at the bottom of the stack: the user's result view. */
export const RESULTS_OWNER = "results";

/** What an owner had on screen: the FEA field selection and the legend. */
export interface SceneColorView {
  fieldName: string | null;
  reduction: string;
  stepIndex: number;
  layer: string | null;
  legendShown: boolean;
  legendMin: number;
  legendMax: number;
}

export interface SceneColorOwner {
  /** `RESULTS_OWNER`, or the id of an owning mode. */
  id: string;
  /**
   * The view to put back for this owner, or null when it has none: an owning
   * mode that has painted nothing of its own is suspended fresh on entry.
   */
  view: SceneColorView | null;
  /**
   * Whether this owner has painted through core since it took the colouring.
   * Always true for `results`; for a mode, set by a paint or a loader landing
   * tagged with its id. Decides whether its view is kept when it leaves.
   */
  painted: boolean;
}

export interface SceneColorOwnerState {
  /** Bottom-up; `stack[0]` is always the results owner. */
  stack: SceneColorOwner[];
  /**
   * The last view of each owning mode that has left the stack after painting,
   * so re-entering it puts that back instead of suspending again. Kept for the
   * life of the page, cleared with the model.
   */
  parked: Record<string, SceneColorView>;
  /** The source whose FEA field the loader last put on screen. */
  loadedSource: string | null;

  /** The id of the owner on top of the stack. */
  activeOwner: () => string;

  /**
   * Enter an owning mode from whatever is on top. `onScreen` is recorded as the
   * view of the owner being covered (if it painted), so leaving the new owner
   * puts it back. Returns the entry now on top: restore its `view` when it has
   * one, suspend the colouring when it does not. Idempotent for the owner
   * already on top.
   */
  push: (id: string, onScreen: SceneColorView) => SceneColorOwner;

  /**
   * Replace the owning mode on top with another, as a mode system does when it
   * switches directly between two owning modes. The leaving mode's view is
   * parked (if it painted) exactly as on `pop`; the owner below is left
   * untouched, so its saved view still restores what the user had before the
   * first of the two. Returns the entry now on top, as `push` does.
   */
  switchTop: (id: string, onScreen: SceneColorView) => SceneColorOwner;

  /**
   * Leave the owning mode on top. Its view is parked when it painted and
   * dropped when it did not. Returns the entry that left and the one now on
   * top, whose `view` (if any) is what to put back on screen. No-op with only
   * `results` on the stack.
   */
  pop: (onScreen: SceneColorView) => { left: SceneColorOwner; below: SceneColorOwner } | null;

  /** A paint tagged by `owner`. Counts only for the owner on top. */
  markPainted: (owner: string) => void;

  /**
   * The owner a load requested now should be tagged with: the owning mode on
   * top when the load repaints the source already on screen, `results` in
   * every other case (no owning mode, or a different or first source). Taken
   * when the load is REQUESTED, so a load in flight keeps the tag of whoever
   * asked for it regardless of which mode is active when it lands.
   */
  requestingOwner: (source: string | null) => string;

  /**
   * The loader put a field for `source` on screen on behalf of `owner`. Returns
   * true when the landed field must be suspended: it arrived under an owning
   * mode that did not ask for it, and is recorded as the tagged owner's view.
   */
  noteLoad: (source: string | null, owner: string, onScreen: SceneColorView) => boolean;

  /**
   * The loader cleared its model. Every saved and parked view referred to it,
   * so they go; the stack itself keeps its order. Whatever loads next is a new
   * source, even the same file again.
   */
  clearViews: () => void;

  /** Forget everything: an empty results-only stack. */
  reset: () => void;
}

function resultsOwner(): SceneColorOwner {
  return { id: RESULTS_OWNER, view: null, painted: true };
}

/**
 * The top entry leaves. Its view is parked when it painted, dropped otherwise.
 *
 * Whether it painted is decided entirely by the tags it reported: a paint
 * through `paintField`, or a loader landing it asked for. Nothing is inferred
 * from what is on screen. This store used to also count an owner as having
 * painted when it left with a field other than the one set aside beneath it -
 * a guess covering a mode that set `fieldName` on the FEA store directly,
 * reporting through neither the loader nor `paintField`. No production path
 * does that: every field change goes through the FEA loader, which tags every
 * landing with the owner that requested it.
 */
function leaveTop(
  state: Pick<SceneColorOwnerState, "stack" | "parked">,
  onScreen: SceneColorView,
): { stack: SceneColorOwner[]; parked: Record<string, SceneColorView>; left: SceneColorOwner } {
  const top = state.stack[state.stack.length - 1];
  const painted = top.painted;
  const left: SceneColorOwner = { ...top, painted, view: painted ? onScreen : null };
  const parked = { ...state.parked };
  if (painted) parked[top.id] = onScreen;
  else delete parked[top.id];
  return { stack: state.stack.slice(0, -1), parked, left };
}

/** A new top entry for `id`: its parked view if it has one, else a fresh one. */
function enterEntry(
  state: Pick<SceneColorOwnerState, "stack" | "parked">,
  id: string,
): { stack: SceneColorOwner[]; parked: Record<string, SceneColorView>; entry: SceneColorOwner } {
  const parkedView = state.parked[id] ?? null;
  const entry: SceneColorOwner = { id, view: parkedView, painted: parkedView !== null };
  const parked = { ...state.parked };
  delete parked[id];
  return { stack: [...state.stack, entry], parked, entry };
}

export const useSceneColorOwnerStore = create<SceneColorOwnerState>((set, get) => ({
  stack: [resultsOwner()],
  parked: {},
  loadedSource: null,

  activeOwner: () => {
    const { stack } = get();
    return stack[stack.length - 1].id;
  },

  push: (id, onScreen) => {
    const state = get();
    const top = state.stack[state.stack.length - 1];
    if (id === RESULTS_OWNER || id === top.id) return top;
    // The owner being covered keeps what it was showing, unless it is a mode
    // that painted nothing: then the screen holds the field set aside under
    // it, which is not its own view.
    const covered: SceneColorOwner = { ...top, view: top.painted ? onScreen : null };
    const next = enterEntry(
      { stack: [...state.stack.slice(0, -1), covered], parked: state.parked },
      id,
    );
    set({ stack: next.stack, parked: next.parked });
    return next.entry;
  },

  switchTop: (id, onScreen) => {
    const state = get();
    const top = state.stack[state.stack.length - 1];
    if (id === RESULTS_OWNER || id === top.id) return top;
    if (top.id === RESULTS_OWNER) return state.push(id, onScreen);
    const gone = leaveTop(state, onScreen);
    const next = enterEntry(gone, id);
    set({ stack: next.stack, parked: next.parked });
    return next.entry;
  },

  pop: (onScreen) => {
    const state = get();
    if (state.stack.length < 2) return null;
    const gone = leaveTop(state, onScreen);
    set({ stack: gone.stack, parked: gone.parked });
    return { left: gone.left, below: gone.stack[gone.stack.length - 1] };
  },

  markPainted: (owner) => {
    const { stack } = get();
    const top = stack[stack.length - 1];
    if (top.id !== owner || top.painted) return;
    set({ stack: [...stack.slice(0, -1), { ...top, painted: true }] });
  },

  requestingOwner: (source) => {
    const state = get();
    const top = state.stack[state.stack.length - 1];
    if (top.id === RESULTS_OWNER) return RESULTS_OWNER;
    return source !== null && source === state.loadedSource ? top.id : RESULTS_OWNER;
  },

  noteLoad: (source, owner, onScreen) => {
    const state = get();
    const stack = state.stack.slice();
    const topIndex = stack.length - 1;
    const top = stack[topIndex];
    if (top.id === RESULTS_OWNER) {
      set({ loadedSource: source });
      return false;
    }
    if (owner === top.id) {
      stack[topIndex] = { ...top, painted: true };
      set({ loadedSource: source, stack });
      return false;
    }
    // Landed under an owning mode that did not ask for it. The tagged owner
    // (the user's results, or a mode further down) now shows this; the mode on
    // top has painted nothing over it yet.
    const ownerIndex = stack.findIndex((entry) => entry.id === owner);
    if (ownerIndex >= 0) {
      stack[ownerIndex] = { ...stack[ownerIndex], view: onScreen, painted: true };
    }
    stack[topIndex] = { ...top, painted: false };
    set({ loadedSource: source, stack });
    return true;
  },

  clearViews: () =>
    set((state) => ({
      loadedSource: null,
      parked: {},
      stack: state.stack.map((entry) => ({
        ...entry,
        view: null,
        painted: entry.id === RESULTS_OWNER,
      })),
    })),

  reset: () => set({ stack: [resultsOwner()], parked: {}, loadedSource: null }),
}));
