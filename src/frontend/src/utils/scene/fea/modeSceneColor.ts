// The active-field arbiter's mode side: who owns the scene colouring right now.
//
// A plugin mode whose whole purpose is its own per-element colouring (an
// engineering-check overlay, a property painter) declares `ownsSceneColor` on
// its PluginModeSpec. While such a mode is active the viewer's FEA field must
// not show underneath it — a field from one analysis read as if it belonged to
// another — and on leaving, the user must find their field exactly as they left
// it, range and legend included. See issue #308.
//
// The same promise runs the other way. Every mode is left as it was found: an
// owning mode that painted something of its own gets that back when you return
// to it, rather than being suspended again as though you had never been there.
// Only the FIRST entry suspends, because only then has the mode painted
// nothing.
//
// Core does the suspending, on the mode's declared behalf. A shell only reports
// the transition (`notifyActiveModeSceneColor`); it never touches scene state
// itself, which keeps a shell's "modes change which tools are offered, not what
// is displayed" contract intact.

import { useColorStore } from "@/state/colorLegendStore";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";

// Loaded on demand: the streaming loader pulls in three + the whole scene
// stack, which neither the boot path nor a unit test should pay for.
async function applyColorsVisible(visible: boolean): Promise<void> {
  try {
    const { setFeaResultColorsVisible } = await import(
      "@/utils/scene/handlers/load_fea_streaming"
    );
    setFeaResultColorsVisible(visible);
  } catch {
    // Only reachable where the scene stack itself cannot load (a non-Vite
    // test runtime); with no scene there is nothing to repaint.
  }
}

/** The one property of a mode this module reads. Matches PluginModeSpec, and a
 * shell's own built-in modes can satisfy it without being plugin modes. */
export interface SceneColorMode {
  id: string;
  ownsSceneColor?: boolean;
}

interface SavedFieldView {
  fieldName: string | null;
  reduction: string;
  stepIndex: number;
  layer: string | null;
  legendShown: boolean;
  legendMin: number;
  legendMax: number;
}

let owner: string | null = null;
let saved: SavedFieldView | null = null;

/**
 * What each owning mode was last showing, so re-entering it puts that back.
 *
 * Suspending is right the FIRST time you enter such a mode — it has painted
 * nothing of its own yet, and a field from another analysis must not sit
 * underneath it. It is wrong every time after. Colour by material in Inspect,
 * glance at Results, come back, and the material colouring was gone: the mode
 * remembered nothing, so every entry was a first entry.
 *
 * Keyed by mode id and kept for the life of the page. A mode the user never
 * painted anything in has no entry and still suspends, which is the old
 * behaviour and the right one.
 */
const ownerViews = new Map<string, SavedFieldView>();

/** Snapshot what is on screen now. */
function snapshot(): SavedFieldView {
  const fea = useFeaAnimationStore.getState();
  const legend = useColorStore.getState();
  return {
    fieldName: fea.fieldName ?? null,
    reduction: fea.reduction,
    stepIndex: fea.stepIndex,
    layer: fea.layer ?? null,
    legendShown: legend.showLegend,
    legendMin: legend.min,
    legendMax: legend.max,
  };
}

/** Put a snapshot back on screen: its field, its legend, its colours. */
function restore(view: SavedFieldView): void {
  const fea = useFeaAnimationStore.getState();
  const legend = useColorStore.getState();

  if (view.fieldName && fea.fieldName !== view.fieldName) {
    // A different field is in the colour buffers. Reload the saved one; the load
    // rebuilds colours, range and legend, and honours the visibility toggle.
    fea.setStepIndex(view.stepIndex);
    if (view.layer) fea.setLayer(view.layer);
    void import("@/utils/scene/fea/resultSelection")
      .then(({ selectFeaResultComponent }) =>
        selectFeaResultComponent(view.fieldName!, view.reduction),
      )
      .catch(() => {
        // The manifest may have been replaced while the mode was away; a
        // vanished field is not an error worth surfacing on a mode switch.
      });
    return;
  }

  // Buffers already hold it: put the visibility and legend back exactly.
  void applyColorsVisible(view.fieldName ? fea.resultColorsVisible : false);
  legend.setMin(view.legendMin);
  legend.setMax(view.legendMax);
  legend.setShowLegend(view.legendShown);
}

/** Suspend: no field colouring, no legend, no change to the user's toggles. */
function suspend(): void {
  // Suppressed without being recorded as a user preference: the store's
  // `resultColorsVisible` toggle stays whatever the user set, and is consulted
  // again on restore.
  void applyColorsVisible(false);
  useColorStore.getState().setShowLegend(false);
}

/** Which mode currently owns the scene colouring, or null. Exposed for tests
 * and for shells that want to render an indicator. */
export function sceneColorOwner(): string | null {
  return owner;
}

/**
 * Report the active mode. Idempotent; call on every mode transition.
 *
 * A mode with `ownsSceneColor` suspends the active FEA field colouring —
 * vertex colours off, legend hidden — without touching the user's own
 * selections or toggles. A mode without it (or `null`, no mode system at all)
 * restores what was suspended: if the owning mode loaded a different field into
 * the buffers, the previously selected field is reloaded; otherwise the
 * colours and legend simply come back. Switching directly between two owning
 * modes keeps the original saved view, so A -> B -> results still restores what
 * the user had before A.
 */
export function notifyActiveModeSceneColor(mode: SceneColorMode | null): void {
  const owns = !!mode?.ownsSceneColor;

  // Whatever is on screen belongs to the mode being left, if that mode owns the
  // colouring. Recorded before anything is changed, so coming back to it shows
  // what was there.
  if (owner !== null && owner !== mode?.id) ownerViews.set(owner, snapshot());

  if (owns) {
    // The view to put back when the LAST owning mode is left. Taken only on the
    // way in from a non-owning mode: owner-to-owner must not overwrite it with
    // the first owner's own painting, or leaving the second would restore the
    // first instead of the user's result.
    if (owner === null) saved = snapshot();
    owner = mode!.id;

    const own = ownerViews.get(owner);
    if (own) restore(own);
    else suspend();
    return;
  }

  if (owner === null) return;
  owner = null;
  const view = saved;
  saved = null;
  if (!view) return;
  restore(view);
}

/** Test hook: forget any suspended state without side effects. */
export function _resetSceneColorOwnerForTests(): void {
  owner = null;
  saved = null;
  // What each owning mode was showing goes too, or one test's Inspect view is
  // restored into the next one's.
  ownerViews.clear();
}
